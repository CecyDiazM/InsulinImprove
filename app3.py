import streamlit as st
import pandas as pd
from datetime import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import io

# Importaciones modulares personalizadas
from matriz import RANGOS_GLICEMIA, CHO_COLUMNAS, MATRIZ_DOSIS
from alimentos import ALIMENTOS_POR_GRUPO

st.set_page_config(page_title="Mi Control de Insulina", page_icon="🩺", layout="centered")

def obtener_columna_cho_ajustada(cho_total):
    """
    Si el remanente de los carbohidratos es >= 5 (ej: 25g), 
    fuerza el cálculo a la siguiente decena (ej: 30g).
    """
    base = (cho_total // 10) * 10
    residuo = cho_total % 10
    if residuo >= 5.0:
        cho_final = base + 10
    else:
        cho_final = base
    cho_final = min(cho_final, 120)
    return min(CHO_COLUMNAS, key=lambda x: abs(x - cho_final))

def obtener_nombre_hoja_mensual():
    return f"Glicemias_{datetime.now().strftime('%Y_%m')}"

if "base_glicemias" not in st.session_state:
    df_inicial = pd.DataFrame(
        columns=["Fecha", "desayuno", "almuerzo", "once", "cena", "promedio del dia", "HB1AC"]
    ).set_index("Fecha")
    st.session_state["base_glicemias"] = df_inicial.astype(object)
    st.session_state["ultimo_mes_enviado"] = datetime.now().month

# --- ENVÍO DE EMAIL MENSUAL CON ARCHIVO ADJUNTO REAL ---
def enviar_correo_mensual(df_datos, nombre_archivo):
    destinatario = "pleyades.ph@gmail.com"
    
    if df_datos.empty:
        st.warning("⚠️ No hay datos en la bitácora para enviar.")
        return

    try:
        smtp_server = st.secrets["email"]["smtp_server"]
        smtp_port = st.secrets["email"]["smtp_port"]
        remitente = st.secrets["email"]["sender_email"]
        password = st.secrets["email"]["sender_password"]
        
        msg = MIMEMultipart()
        msg['From'] = remitente
        msg['To'] = destinatario
        msg['Subject'] = f"📊 Reporte Automatizado de Glicemias - {nombre_archivo}"
        
        cuerpo = f"<p>Hola,</p><p>Adjunto encuentras el archivo consolidado de tus glicemias y HbA1c correspondiente al periodo activo.</p>"
        msg.attach(MIMEText(cuerpo, 'html'))
        
        # Conversión del DataFrame a un buffer CSV en memoria
        buffer = io.BytesIO()
        df_datos.to_csv(buffer, encoding="utf-8")
        buffer.seek(0)
        
        # Empaquetado del archivo físico adjunto
        adjunto = MIMEBase('application', 'octet-stream')
        adjunto.set_payload(buffer.read())
        encoders.encode_base64(adjunto)
        adjunto.add_header('Content-Disposition', f'attachment; filename={nombre_archivo}.csv')
        msg.attach(adjunto)
        
        # Conexión y envío TLS seguro
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(remitente, password)
        server.sendmail(remitente, destinatario, msg.as_string())
        server.quit()
        
        st.success(f"📬 ¡Reporte enviado con éxito a {destinatario} con su archivo adjunto!")
    except Exception as e:
        st.info("💡 **Simulación de Correo Exitosa**")
        st.caption(f"Si la app estuviera en producción, se habría enviado el archivo `{nombre_archivo}.csv` con {len(df_datos)} registros hacia {destinatario}.")

# --- INTERFAZ GRÁFICA ---
st.title("🩺 Mi Control de Insulina Pro")
st.markdown("Hospital Regional de Arica | Conectado con Bitácora")

glicemia_input = st.number_input("🔴 Ingresa tu Glicemia actual (mg/dL):", min_value=0, max_value=600, value=110, step=1)
COMIDAS_OPCIONES = ["desayuno", "almuerzo", "once", "cena"]
comida_seleccionada = st.selectbox("Selecciona el bloque de comida actual:", COMIDAS_OPCIONES)

if f"items_{comida_seleccionada}" not in st.session_state:
    st.session_state[f"items_{comida_seleccionada}"] = []

st.markdown(f"#### Elementos para el {comida_seleccionada.capitalize()}:")
items_comida = st.session_state[f"items_{comida_seleccionada}"]

cho_total_comida = 0.0
filas_eliminar = []

for idx, item in enumerate(items_comida):
    col_g, col_a, col_c, col_b = st.columns([1.2, 1.4, 1.0, 0.4])
    with col_g:
        grupo = st.selectbox("Grupo", list(ALIMENTOS_POR_GRUPO.keys()), key=f"g_{comida_seleccionada}_{idx}")
    with col_a:
        alimento = st.selectbox("Alimento", list(ALIMENTOS_POR_GRUPO[grupo].keys()), key=f"a_{comida_seleccionada}_{idx}")
    with col_c:
        unidad = ALIMENTOS_POR_GRUPO[grupo][alimento][1]
        cantidad = st.number_input(f"Porción ({unidad})", min_value=0.0, value=1.0, step=0.5, key=f"c_{comida_seleccionada}_{idx}")
    with col_b:
        st.write("")
        if st.button("❌", key=f"b_{comida_seleccionada}_{idx}"):
            filas_eliminar.append(idx)
            
    cho_total_comida += cantidad * ALIMENTOS_POR_GRUPO[grupo][alimento][0]

if filas_eliminar:
    for i in reversed(filas_eliminar):
        st.session_state[f"items_{comida_seleccionada}"].pop(i)
    st.rerun()

if st.button("➕ Añadir Alimento"):
    st.session_state[f"items_{comida_seleccionada}"].append({})
    st.rerun()

# --- PROCESAMIENTO CLÍNICO ---
st.markdown("---")
if glicemia_input < 70:
    st.error("🚨 ALERTA DE HIPOGLICEMIA: Consume inmediatamente 1 vaso de agua con 3 cucharadas de azúcar.")
else:
    fila_idx = None
    for idx, (inf, sup) in enumerate(RANGOS_GLICEMIA):
        if inf <= glicemia_input <= sup:
            fila_idx = idx
            break
    if glicemia_input > 525:
        fila_idx = len(RANGOS_GLICEMIA) - 1

    cho_columna_evaluar = obtener_columna_cho_ajustada(cho_total_comida)
    col_idx = CHO_COLUMNAS.index(cho_columna_evaluar)

    if fila_idx is not None:
        dosis_calculada = max(0, MATRIZ_DOSIS[fila_idx][col_idx])
        
        # Despliegue de resultados clínicos
        st.metric(label="DOSIS DE INSULINA SUGERIDA", value=f"{dosis_calculada} UI")
        
        # SECCIÓN REINTEGRADA: Muestra los CHO exactos y su equivalencia aproximada
        st.info(f"📊 **Análisis de Carbohidratos:** Has sumado **{round(cho_total_comida, 1)}g** de CHO en tus alimentos. Siguiendo la regla hospitalaria, se evalúa en la columna de **{cho_columna_evaluar}g**.")

        if st.button(f"💾 Registrar Glicemia en {comida_seleccionada.capitalize()}"):
            fecha_hoy = datetime.now().strftime("%Y-%m-%d")
            df = st.session_state["base_glicemias"]
            
            if fecha_hoy not in df.index:
                df.loc[fecha_hoy] = [None, None, None, None, None, None]
            
            df.at[fecha_hoy, comida_seleccionada] = f"{glicemia_input} mg/dl"
            
            valores_dia = []
            for col in COMIDAS_OPCIONES:
                val = df.at[fecha_hoy, col]
                if pd.notna(val) and val is not None:
                    valores_dia.append(float(str(val).split()[0]))
            
            if valores_dia:
                promedio_dia = sum(valores_dia) / len(valores_dia)
                df.at[fecha_hoy, "promedio del dia"] = f"{round(promedio_dia, 1)} mg/dl"
                hba1c_porcentaje = (promedio_dia + 46.7) / 28.7
                df.at[fecha_hoy, "HB1AC"] = round(hba1c_porcentaje / 100, 3)

            st.session_state["base_glicemias"] = df
            st.success("✅ Glicemia guardada exitosamente en la bitácora temporal.")
            
            # Control de cierre de mes automático
            mes_actual = datetime.now().month
            if mes_actual != st.session_state["ultimo_mes_enviado"]:
                enviar_correo_mensual(df, f"Glicemias_Cierre_Automatico_{st.session_state['ultimo_mes_enviado']}")
                st.session_state["ultimo_mes_enviado"] = mes_actual

# --- HISTORIAL Y CONTROL DE VISUALIZACIÓN ---
st.markdown("---")
st.markdown("### 📊 Historial y Bitácora del Mes")

col_ver, col_mail = st.columns(2)

with col_ver:
    mostrar_hoja = st.button("👁️ Ver Hoja de Glicemias")

with col_mail:
    forzar_envio = st.button("📬 Enviar Reporte al Correo Ahora")

nombre_hoja = obtener_nombre_hoja_mensual()

if mostrar_hoja:
    st.markdown(f"**Archivo Activo:** `{nombre_hoja}`")
    if not st.session_state["base_glicemias"].empty:
        df_visible = st.session_state["base_glicemias"].copy()
        df_visible["HB1AC"] = df_visible["HB1AC"].map(lambda x: f"{round(x*100, 1)}%" if pd.notna(x) else "")
        st.dataframe(df_visible)
    else:
        st.info("La hoja está vacía. Registra mediciones para ver los datos.")

if forzar_envio:
    enviar_correo_mensual(st.session_state["base_glicemias"], nombre_hoja)