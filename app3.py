import streamlit as st
import pandas as pd
from datetime import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import io
import gspread
from google.oauth2.service_account import Credentials

# Importaciones modulares personalizadas
from matriz import RANGOS_GLICEMIA, CHO_COLUMNAS, MATRIZ_DOSIS
from alimentos import ALIMENTOS_POR_GRUPO

st.set_page_config(page_title="Mi Control de Insulina", page_icon="🩺", layout="centered")

# --- 1. FUNCIÓN PARA AUTENTICARSE CON GOOGLE CLOUD ---
def conectar_sheets():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    client = gspread.authorize(creds)
    return client


# --- 2. FUNCIÓN PRINCIPAL PARA GUARDAR O ACTUALIZAR UNA GLICEMIA ---
def guardar_glicemia_en_sheets(bloque_horario, valor_glicemia):
    try:
        client = conectar_sheets()
        spreadsheet = client.open("Bitacora_Glicemias")

        ahora = datetime.now()
        meses_es = {
            1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
            5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
            9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
        }
        nombre_pestana = f"{meses_es[ahora.month]}-{ahora.year}"

        try:
            worksheet = spreadsheet.worksheet(nombre_pestana)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=nombre_pestana, rows="100", cols="7")
            encabezados = ["Fecha", "Desayuno", "Almuerzo", "Once", "Cena", "Promedio del Dia", "HB1AC"]
            worksheet.append_row(encabezados)

        fechas_registradas = worksheet.col_values(1)
        fecha_hoy = ahora.strftime("%Y-%m-%d")

        columnas_comidas = {
            "Desayuno": 2,
            "Almuerzo": 3,
            "Once": 4,
            "Cena": 5,
        }
        col_idx = columnas_comidas.get(bloque_horario)

        if not col_idx:
            st.error("Bloque horario inválido.")
            return

        if fecha_hoy in fechas_registradas:
            row_idx = fechas_registradas.index(fecha_hoy) + 1
            worksheet.update_cell(row_idx, col_idx, f"{valor_glicemia} mg/dl")
        else:
            nueva_fila = ["", "", "", "", "", "", ""]
            nueva_fila[0] = fecha_hoy
            nueva_fila[col_idx - 1] = f"{valor_glicemia} mg/dl"
            worksheet.append_row(nueva_fila)
            fechas_registradas = worksheet.col_values(1)
            row_idx = fechas_registradas.index(fecha_hoy) + 1

        # Recalcular Promedio e HbA1c en tiempo real en la fila
        valores_fila = worksheet.row_values(row_idx)
        while len(valores_fila) < 5:
            valores_fila.append("")

        glicemias_numericas = []
        for celda in valores_fila[1:5]:
            if celda and "mg/dl" in celda:
                try:
                    num = int(celda.replace("mg/dl", "").strip())
                    glicemias_numericas.append(num)
                except ValueError:
                    pass

        if glicemias_numericas:
            promedio_dia = sum(glicemias_numericas) / len(glicemias_numericas)
            hb1ac = (promedio_dia + 46.7) / 28.7

            worksheet.update_cell(row_idx, 6, f"{int(promedio_dia)} mg/dl")
            worksheet.update_cell(row_idx, 7, f"{hb1ac:.2f}%")

        st.success(f"🎯 Guardado permanentemente en Google Sheets ({bloque_horario}): {valor_glicemia} mg/dl")
    except Exception as e:
        st.error(f"❌ Error al interactuar con Google Sheets: {e}")


# --- 3. FUNCIÓN PARA LEER LOS DATOS EN VIVO DESDE GOOGLE SHEETS ---
def obtener_historial_sheets():
    try:
        client = conectar_sheets()
        spreadsheet = client.open("Bitacora_Glicemias")
        ahora = datetime.now()
        meses_es = {
            1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
            5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
            9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
        }
        nombre_pestana = f"{meses_es[ahora.month]}-{ahora.year}"
        worksheet = spreadsheet.worksheet(nombre_pestana)
        records = worksheet.get_all_records()
        return pd.DataFrame(records)
    except Exception:
        return pd.DataFrame()


def obtener_columna_cho_ajustada(cho_total):
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

# Inicialización de estados de control mensual
if "ultimo_mes_enviado" not in st.session_state:
    st.session_state["ultimo_mes_enviado"] = datetime.now().month

# --- ENVÍO DE EMAIL CON LOS DATOS REALES DE GOOGLE SHEETS ---
def enviar_correo_mensual(df_datos, nombre_archivo):
    destinatario = "pleyades.ph@gmail.com"
    
    if df_datos.empty:
        st.warning("⚠️ No hay datos guardados para enviar por correo.")
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
        
        cuerpo = f"<p>Hola,</p><p>Adjunto encuentras el archivo consolidado de tus glicemias y HbA1c extraído directamente desde tu base de datos en la nube.</p>"
        msg.attach(MIMEText(cuerpo, 'html'))
        
        buffer = io.BytesIO()
        df_datos.to_csv(buffer, index=False, encoding="utf-8")
        buffer.seek(0)
        
        adjunto = MIMEBase('application', 'octet-stream')
        adjunto.set_payload(buffer.read())
        encoders.encode_base64(adjunto)
        adjunto.add_header('Content-Disposition', f'attachment; filename={nombre_archivo}.csv')
        msg.attach(adjunto)
        
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(remitente, password)
        server.sendmail(remitente, destinatario, msg.as_string())
        server.quit()
        
        st.success(f"📬 ¡Reporte enviado con éxito a {destinatario} con los datos de Google Sheets!")
    except Exception as e:
        st.info("💡 **Simulación de Correo Exitosa**")
        st.caption(f"Si la app estuviera en producción, se habría enviado el archivo `{nombre_archivo}.csv` con {len(df_datos)} registros hacia {destinatario}. Detalle: {e}")

# --- INTERFAZ GRÁFICA ---
st.title("🩺 Mi Control de Insulina Pro")
st.markdown("Hospital Regional de Arica | Conectado con Bitácora Cloud")

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
        st.metric(label="DOSIS DE INSULINA SUGERIDA", value=f"{dosis_calculada} UI")
        st.info(f"📊 **Análisis de Carbohidratos:** Has sumado **{round(cho_total_comida, 1)}g** de CHO en tus alimentos. Evaluando en la columna de **{cho_columna_evaluar}g**.")

        # --- CAMBIO IMPORTANTE: SE CONECTA EL BOTÓN A GOOGLE SHEETS ---
        if st.button(f"💾 Registrar Glicemia en {comida_seleccionada.capitalize()}"):
            # Enviamos el bloque capitalizado ("Desayuno", "Almuerzo"...) para que gspread lo reconozca
            guardar_glicemia_en_sheets(comida_seleccionada.capitalize(), glicemia_input)
            
            # Control de cierre de mes automático usando los datos guardados
            mes_actual = datetime.now().month
            if mes_actual != st.session_state["ultimo_mes_enviado"]:
                df_cierre = obtener_historial_sheets()
                enviar_correo_mensual(df_cierre, f"Glicemias_Cierre_Automatico_{st.session_state['ultimo_mes_enviado']}")
                st.session_state["ultimo_mes_enviado"] = mes_actual

# --- HISTORIAL EN VIVO Y CONTROL DE EMAIL ---
st.markdown("---")
st.markdown("### 📊 Historial y Bitácora del Mes")

col_ver, col_mail = st.columns(2)

with col_ver:
    mostrar_hoja = st.button("👁️ Ver Hoja de Glicemias")

with col_mail:
    forzar_envio = st.button("📬 Enviar Reporte al Correo Ahora")

nombre_hoja = obtener_nombre_hoja_mensual()

# --- CAMBIO IMPORTANTE: LEER DIRECTAMENTE DESDE LA BASE DE DATOS EN LA NUBE ---
if mostrar_hoja:
    st.markdown(f"**Base de Datos Activa (Google Sheets):** Pestaña actual del mes")
    df_sheets = obtener_historial_sheets()
    if not df_sheets.empty:
        st.dataframe(df_sheets, use_container_width=True)
    else:
        st.info("La planilla está vacía o aún no se han registrado mediciones este mes.")

if forzar_envio:
    df_sheets = obtener_historial_sheets()
    if not df_sheets.empty:
        enviar_correo_mensual(df_sheets, nombre_hoja)
    else:
        st.warning("⚠️ No hay datos en Google Sheets para poder enviar el reporte.")