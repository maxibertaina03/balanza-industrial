# ================================================
# SISTEMA DE PESAJE INDUSTRIAL - BALANZA MULTIUSUARIO
# Versión comentada y explicada línea por línea
# Autor: Máximo Bertaina
# ================================================

import streamlit as st
import pandas as pd
import json
import os
import time
from datetime import datetime
import random
import plotly.express as px
import threading
import serial
import re


# ------------------- CONFIGURACIÓN DE LA PÁGINA -------------------

st.set_page_config(
    page_title="Sistema de Pesaje Industrial",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ------------------- ESTILOS PERSONALIZADOS (CSS) -------------------

st.markdown("""
<style>
    .big-weight {
        font-size: 4rem;
        font-weight: bold;
        text-align: center;
        padding: 2rem;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-radius: 10px;
        margin: 1rem 0;
        animation: pulse 2s ease-in-out infinite;
    }
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.9; }
    }
    .metric-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 8px;
        border-left: 4px solid #667eea;
    }
    .stButton>button {
        width: 100%;
    }
</style>
""", unsafe_allow_html=True)

# ------------------- ARCHIVOS DE CONFIGURACIÓN -------------------

CONFIG_FILE = "balanza_config.json"        # Guarda historial, expediciones y último producto
REALTIME_FILE = "balanza_realtime.json"    # Comparte peso en tiempo real entre usuarios
PASSWORD_FILE = "balanza_password.json"    # Guarda la contraseña del modo servidor


# ------------------- PESOS TEÓRICOS -------------------
# Peso en kg de cada caja según el tipo de queso


PRODUCT_TO_WEIGHT = {
    "CREMOSO LAS TRES ESTRELLAS": 0.35,
    "CREMOSO SABORLAC": 0.35,
    "CREMOSO MEDIA HORMA": 0.35,
    "POR SALUT LAS TRES ESTRELLAS": 0.35,
    "P.SALUT CON CHIA Y LINO": 0.4,
    "PROQUESO-FIT": 0.4,
    "TYBO LAS TRES": 0.4,
    "MUZZARELLA SABORLAC": 0.4,
    "MUZZARELLA LAS TRES ESTRELLAS": 0.4,
    "MOZZARELLA BLOCK": 0.4,
    "PATEGRAS SABORLAC": 0.35,
    "AZUL LAS TRES ESTRELLAS": 0.28,
    "CHEDDAR": 0.4,
    "GRUYERITO EN CUÑA": 0.55,
    "ROMANITO": 0.3,
    "SARDO LAS TRES ESTRELLAS": 0.3,
    "SARDO SABORLAC": 0.3,
    "REGGIANITO HORMA": 0.35,
    "REGGIANITO BLOCK": 0.4,
    "PROVOLONE HILADO": 0.35,
    "PROVOLONE HILADO EN FETAS": 0.35,
    "RICOTTA EN HORMA": 0.35,
    "RICOTTA CABRAL": 0.38,
    "SARDO SABORLAC BLOCK": 0.4
}

# Peso en kg de cada tipo de bandeja/plástico

TRAY_WEIGHTS = {
    "Bandeja de Cremoso": 1.7,
    "Bandeja de Barra": 1.4,
    "Bandeja de Sardo": 2.0,
    "Sin Bandeja": 0.0
}

# ------------------- FUNCIONES AUXILIARES -------------------

def hexdump(b):
    return ' '.join(f'{c:02X}' for c in b)


# =================================================================
# PARSEO DE BALANZA EL05 - FORMATO: b'M000010\r' → 1.0 kg
# =================================================================


def parse_el05_corregido(data_bytes):
    """
    Parsea el formato M000010 (donde 000010 representa el peso)
    Basado en el debug: DEBUG BALANZA RAW (EL05): b'M000010\r'
    """
    try:
        # Convertir a string y limpiar
        data_str = data_bytes.decode('ascii', errors='ignore').strip()
        print(f"DEBUG parse_el05_corregido: RAW='{data_str}'")  # Debug adicional
        
        # El formato parece ser: MXXXXXX donde XXXXXX es el peso
        # Buscar cualquier secuencia de dígitos en el string
        import re
        match = re.search(r'(\d+)', data_str)
        
        if match:
            digits = match.group(1)
            print(f"DEBUG: Dígitos encontrados: '{digits}'")  # Debug
            
            # Convertir a número
            raw_value = int(digits)
            
            # Ajustar decimales - probar diferentes factores
            # Dependiendo de la balanza, puede ser /10, /100, /1000
            peso_val = raw_value / 10.0  # ← PRUEBA PRIMERO ESTO
            print(f"DEBUG: raw_value={raw_value}, peso_val={peso_val}")  # Debug
            
            return {
                "raw": data_bytes,
                "hex": hexdump(data_bytes),
                "peso_str": data_str,
                "peso_val": peso_val,
                "digits": digits,
                "raw_value": raw_value
            }
        else:
            print(f"DEBUG: No se encontraron dígitos en '{data_str}'")
            return None
            
    except Exception as e:
        print(f"DEBUG ERROR en parse_el05_corregido: {e}")
        return None


# =================================================================
# PARSEO DE BALANZA COND (formato estándar con signo y unidad)
# =================================================================


def parse_cond(line_bytes):
    try:
        s = line_bytes.decode('ascii', errors='replace').strip('\r\n')
    except:
        s = ''
    if s and ord(s[0]) == 2:
        s = s[1:]
    sign = 1
    if s.startswith('-'):
        sign = -1
        s = s[1:]
    m = re.search(r'(-?\d+(\.\d+)?)', s)
    peso_val = None
    if m:
        try:
            peso_val = float(m.group(1)) * sign
        except:
            peso_val = None
    unidad = None
    tipo = None
    m2 = re.search(r'([KLkl])', s)
    if m2:
        unidad = m2.group(1).upper()
    m3 = re.search(r'([GNgn])', s)
    if m3:
        tipo = m3.group(1).strip().upper()
    return {
        "raw_bytes": line_bytes,
        "hex": hexdump(line_bytes),
        "payload": s,
        "peso_val": peso_val,
        "unidad": unidad,
        "tipo": tipo
    }

# =================================================================
# COMUNICACIÓN EN TIEMPO REAL (archivo compartido entre usuarios)
# =================================================================


def read_realtime_data():
    try:
        if os.path.exists(REALTIME_FILE):
            with open(REALTIME_FILE, 'r') as f:
                data = json.load(f)
                return data
    except:
        pass
    return {
        "peso": 0.0,
        "reading": False,
        "last_update": time.time(),
        "status": "Detenido"
    }

def write_realtime_data(peso, reading, status="Leyendo"):
    try:
        data = {
            "peso": peso,
            "reading": reading,
            "last_update": time.time(),
            "status": status
        }
        with open(REALTIME_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Error escribiendo datos en tiempo real: {e}")


# =================================================================
# HILO QUE LEE LA BALANZA CONTINUAMENTE
# =================================================================


def continuous_reading(port, baud, formato):
    """Thread que lee continuamente de la balanza y actualiza el archivo compartido"""
    SIMULATE = True  # Cambia a True para probar sin balanza real

    if SIMULATE:
        # --- MODO SIMULACIÓN ---
        while True:
            realtime = read_realtime_data()
            if not realtime['reading']:
                time.sleep(0.5)
                continue
            peso = round(random.uniform(50.0, 500.0), 2)
            write_realtime_data(peso, False, "Leyendo (Simulación)")
            time.sleep(1.5)
    else:
        # --- MODO REAL ---
        ser = None
        try:
            ser = serial.Serial(
                port=port,
                baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1
            )
            time.sleep(2)  # Espera estabilización
            write_realtime_data(0.0, True, f"Conectado a {port}")

            while True:
                realtime = read_realtime_data()
                if not realtime['reading']:
                    time.sleep(0.5)
                    continue

                try:
                    if formato == "el05":
                        raw = ser.read_until(b'\r')
                        print(f"DEBUG BALANZA RAW (EL05): {raw}")
                        
                        # ← USAR LA NUEVA FUNCIÓN CORREGIDA
                        parsed = parse_el05_corregido(raw)
                        
                        if parsed and parsed['peso_val'] is not None:
                            peso = parsed['peso_val']
                            print(f"DEBUG: Peso parseado: {peso} kg")
                            write_realtime_data(peso, True, f"Leyendo: {peso:.2f} kg")
                        else:
                            print(f"DEBUG: No se pudo parsear el peso")
                            write_realtime_data(0.0, True, "Esperando datos válidos")

                    elif formato == "cond":
                        raw = ser.read_until(b'\n')
                        print(f"DEBUG BALANZA RAW (COND): {raw}")
                        parsed = parse_cond(raw)
                        if parsed and parsed['peso_val'] is not None:
                            peso = parsed['peso_val']
                            write_realtime_data(peso, True, f"Leyendo: {peso:.2f} kg")
                        else:
                            write_realtime_data(0.0, True, "Dato inválido")

                    time.sleep(0.1)

                except serial.SerialException as e:
                    write_realtime_data(0.0, False, f"Error puerto: {e}")
                    break
                except Exception as e:
                    print(f"DEBUG ERROR: {e}")
                    write_realtime_data(0.0, True, f"Error: {e}")
                    time.sleep(1)

        except serial.SerialException as e:
            write_realtime_data(0.0, False, f"Error conexión: {e}")
        except Exception as e:
            write_realtime_data(0.0, False, f"Error: {e}")
        finally:
            if ser and ser.is_open:
                ser.close()
            write_realtime_data(0.0, False, "Desconectado")

# ← NUEVO: Función para probar diferentes factores de escala
def probar_factor_escala():
    """Función para ayudar a determinar el factor de escala correcto"""
    test_data = b'M000010\r'  # Tu dato de ejemplo
    parsed = parse_el05_corregido(test_data)
    
    if parsed:
        raw_value = parsed['raw_value']
        print(f"\n=== PRUEBA FACTOR ESCALA ===")
        print(f"Dato RAW: {test_data}")
        print(f"Valor numérico: {raw_value}")
        print(f"División por 10: {raw_value / 10.0} kg")
        print(f"División por 100: {raw_value / 100.0} kg") 
        print(f"División por 1000: {raw_value / 1000.0} kg")
        print(f"División por 10000: {raw_value / 10000.0} kg")
        print("============================\n")

# Ejecutar prueba al inicio
probar_factor_escala()

# ------------------- INICIALIZACIÓN DE ESTADOS -------------------


if 'history_list' not in st.session_state:
    st.session_state.history_list = []
if 'expeditions' not in st.session_state:
    st.session_state.expeditions = []
if 'last_product' not in st.session_state:
    st.session_state.last_product = ""
if 'is_server' not in st.session_state:
    st.session_state.is_server = False
if 'reading_thread' not in st.session_state:
    st.session_state.reading_thread = None
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
if 'password' not in st.session_state:
    st.session_state.password = "admin123"  # Por defecto


# ------------------- GESTIÓN DE CONTRASEÑA -------------------

def load_password():
    try:
        if os.path.exists(PASSWORD_FILE):
            with open(PASSWORD_FILE, 'r') as f:
                data = json.load(f)
                return data.get("password", "admin123")
    except:
        pass
    return "admin123"

def save_password(password):
    try:
        data = {"password": password}
        with open(PASSWORD_FILE, 'w') as f:
            json.dump(data, f)
        return True
    except:
        return False

# ------------------- CARGA Y GUARDADO DE DATOS -------------------

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                st.session_state.history_list = data.get("current_history", [])
                st.session_state.expeditions = data.get("expeditions", [])
                st.session_state.last_product = data.get("last_product", "")
                
                for entry in st.session_state.history_list:
                    if 'timestamp' not in entry:
                        entry['timestamp'] = "Sin fecha"
                    if 'lote' not in entry:
                        entry['lote'] = ""
                    if 'hormas' not in entry:
                        entry['hormas'] = 0
                
                for exp in st.session_state.expeditions:
                    for entry in exp.get('records', []):
                        if 'timestamp' not in entry:
                            entry['timestamp'] = "Sin fecha"
                        if 'lote' not in entry:
                            entry['lote'] = ""
                        if 'hormas' not in entry:
                            entry['hormas'] = 0
                            
        except Exception as e:
            st.error(f"Error cargando configuración: {e}")

def save_config():
    data = {
        "current_history": st.session_state.history_list,
        "expeditions": st.session_state.expeditions,
        "last_product": st.session_state.last_product
    }
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"Error guardando configuración: {e}")


# ------------------- FUNCIONES DE EDICIÓN -------------------


def editar_registro(lista, index, nuevo_registro):
    """Reemplaza un registro y limpia el estado de edición"""
    if 0 <= index < len(lista):
        lista[index] = nuevo_registro
        save_config()
        st.success("Registro actualizado correctamente")
        
        # LIMPIAR TODOS los estados de edición relacionados con este índice
        keys_to_clear = [k for k in st.session_state.keys() if k.startswith(f"editing_hist_{index}") or k.startswith(f"editing_exp_") and str(index) in k]
        for key in keys_to_clear:
            del st.session_state[key]
        
        # También limpiar el índice global de expedición si existe
        if "editing_exp_index" in st.session_state:
            del st.session_state.editing_exp_index
            
        time.sleep(1.2)
        st.rerun()

def eliminar_registro(lista, index):
    """Elimina un registro y limpia estados"""
    if 0 <= index < len(lista):
        eliminado = lista.pop(index)
        save_config()
        st.success(f"Registro eliminado: {eliminado['producto']} – {eliminado['neto']:.2f} kg")
        
        # Limpiar estados de edición del índice eliminado y superiores
        keys_to_clear = [k for k in st.session_state.keys() 
                        if (k.startswith("editing_hist_") or k.startswith("editing_exp_")) 
                        and int(k.split("_")[-1]) >= index]
        for key in keys_to_clear:
            del st.session_state[key]
            
        if "editing_exp_index" in st.session_state:
            del st.session_state.editing_exp_index
            
        time.sleep(1.2)
        st.rerun()
        
        
def mostrar_formulario_edicion(registro_original, index, es_historial=True):
    """Formulario de edición con Cancelar funcional"""
    exp_index = st.session_state.get("editing_exp_index", None)
    
    with st.form(key=f"form_edit_final_{index}_{'hist' if es_historial else 'exp'}"):
        st.write("### Editar Registro")
        
        producto = st.selectbox("Producto", options=list(PRODUCT_TO_WEIGHT.keys()),
                               index=list(PRODUCT_TO_WEIGHT.keys()).index(registro_original['producto']),
                               key=f"prod_{index}")
        cajas = st.number_input("Cajas", min_value=0, value=registro_original['cajas'], key=f"cajas_{index}")
        bandeja = st.selectbox("Bandeja", options=list(TRAY_WEIGHTS.keys()),
                              index=list(TRAY_WEIGHTS.keys()).index(registro_original['bandeja']),
                              key=f"band_{index}")
        cant_bandejas = st.number_input("Cant. Bandejas", min_value=0, value=registro_original['cant_bandeja'], key=f"cantb_{index}")
        pallet = st.number_input("Pallet (kg)", min_value=0.0, value=float(registro_original['pallet']), step=0.1, key=f"pallet_{index}")
        lote = st.text_input("Lote", value=registro_original.get('lote', ''), key=f"lote_{index}")
        hormas = st.number_input("Hormas", min_value=0, value=registro_original.get('hormas', 200), key=f"hormas_{index}")
        bruto = st.number_input("Peso Bruto (kg)", min_value=0.0, value=float(registro_original['bruto']), step=0.01,
                                help="Modificar solo si la balanza falló", key=f"bruto_{index}")

        col1, col2 = st.columns(2)
        with col1:
            if st.form_submit_button("Guardar Cambios", type="primary", use_container_width=True):
                peso_cajas = cajas * PRODUCT_TO_WEIGHT[producto]
                peso_bandejas = cant_bandejas * TRAY_WEIGHTS[bandeja]
                neto = bruto - pallet - peso_cajas - peso_bandejas

                nuevo = {
                    'producto': producto,
                    'cajas': cajas,
                    'bandeja': bandeja,
                    'cant_bandeja': cant_bandejas,
                    'pallet': pallet,
                    'bruto': round(bruto, 3),
                    'neto': round(neto, 3),
                    'lote': lote,
                    'hormas': hormas,
                    'timestamp': registro_original['timestamp']
                }

                if es_historial:
                    editar_registro(st.session_state.history_list, index, nuevo)
                else:
                    editar_registro(st.session_state.expeditions[exp_index]['records'], index, nuevo)

        with col2:
            if st.form_submit_button("Cancelar", type="secondary", use_container_width=True):
                # LIMPIAR estado de edición
                key_pattern = f"editing_{'hist' if es_historial else 'exp'}_{index}"
                keys_to_delete = [k for k in st.session_state.keys() if key_pattern in k or k == "editing_exp_index"]
                for k in keys_to_delete:
                    del st.session_state[k]
                st.success("Edición cancelada")
                time.sleep(0.8)
                st.rerun()


# ------------------- CARGA INICIAL -------------------

if 'config_loaded' not in st.session_state:
    load_config()
    st.session_state.config_loaded = True
    st.session_state.password = load_password()

# ------------------- LECTURA DEL PESO EN TIEMPO REAL -------------------

realtime_data = read_realtime_data()
current_peso = realtime_data['peso']
is_reading = realtime_data['reading']
status_text = realtime_data['status']

# =================================================================
# SIDEBAR - PANEL DE CONTROL
# =================================================================

st.sidebar.title("⚖️ Control de Balanza")

# ----------- Autenticación para modo servidor -------------------

if st.session_state.is_server and not st.session_state.authenticated:
    st.sidebar.warning("🔒 Autenticación Requerida")
    st.sidebar.markdown("### Ingrese la contraseña para modo Servidor")
    
    password_input = st.sidebar.text_input("Contraseña", type="password")
    
    if st.sidebar.button("🔑 Autenticar"):
        if password_input == st.session_state.password:
            st.session_state.authenticated = True
            st.sidebar.success("✅ Autenticación exitosa")
            time.sleep(1)
            st.rerun()
        else:
            st.sidebar.error("❌ Contraseña incorrecta")
            st.session_state.is_server = False
            time.sleep(2)
            st.rerun()

# Selector de modo
if not st.session_state.is_server or not st.session_state.authenticated:
    mode = st.sidebar.radio("Modo de operación", ["Cliente (solo lectura)", "Servidor (controlar balanza)"], index=0)
    st.session_state.is_server = (mode == "Servidor (controlar balanza)")
    
    if st.session_state.is_server and not st.session_state.authenticated:
        st.sidebar.info("🔒 Seleccione 'Servidor' y luego ingrese la contraseña")

# Controles de servidor
if st.session_state.is_server and st.session_state.authenticated:
    st.sidebar.success("🖥️ Modo SERVIDOR activo")
    
    if st.sidebar.button("🔐 Cambiar Contraseña"):
        st.session_state.show_password_change = True
    
    if st.session_state.get('show_password_change', False):
        st.sidebar.markdown("### Cambiar Contraseña")
        new_password = st.sidebar.text_input("Nueva Contraseña", type="password")
        confirm_password = st.sidebar.text_input("Confirmar Contraseña", type="password")
        
        col1, col2 = st.sidebar.columns(2)
        with col1:
            if st.button("💾 Guardar"):
                if new_password and new_password == confirm_password:
                    if save_password(new_password):
                        st.session_state.password = new_password
                        st.sidebar.success("✅ Contraseña actualizada")
                        st.session_state.show_password_change = False
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.sidebar.error("❌ Error guardando contraseña")
                else:
                    st.sidebar.error("❌ Las contraseñas no coinciden")
        with col2:
            if st.button("❌ Cancelar"):
                st.session_state.show_password_change = False
                st.rerun()
    
    st.sidebar.markdown("### Conexión")
    
    if 'serial_port' not in st.session_state:
        st.session_state.serial_port = "COM4"
    if 'serial_baud' not in st.session_state:
        st.session_state.serial_baud = 9600
    if 'serial_format' not in st.session_state:
        st.session_state.serial_format = "el05"

    st.session_state.serial_port = st.sidebar.text_input("Puerto", st.session_state.serial_port)
    st.session_state.serial_baud = st.sidebar.selectbox("Baud Rate", [9600, 19200, 38400], 
        index=[9600, 19200, 38400].index(st.session_state.serial_baud))
    st.session_state.serial_format = st.sidebar.selectbox("Formato", ["el05", "cond"], 
        index=["el05", "cond"].index(st.session_state.serial_format))
    
    col1, col2 = st.sidebar.columns(2)
    with col1:
        if st.button("Iniciar", key="start_btn", disabled=is_reading):
            write_realtime_data(0.0, True, "Iniciando...")
            
            if st.session_state.reading_thread is None or not st.session_state.reading_thread.is_alive():
                st.session_state.reading_thread = threading.Thread(
                    target=continuous_reading,
                    args=(
                        st.session_state.serial_port,
                        st.session_state.serial_baud,
                        st.session_state.serial_format
                    ),
                    daemon=True
                )
                st.session_state.reading_thread.start()
            
            st.rerun()
    
    with col2:
        if st.button("⏹️ Detener", disabled=not is_reading):
            write_realtime_data(0.0, False, "Detenido")
            st.rerun()
    
    if st.sidebar.button("🚪 Cerrar Sesión Servidor"):
        st.session_state.authenticated = False
        st.session_state.is_server = False
        write_realtime_data(0.0, False, "Detenido (Sesión cerrada)")
        st.sidebar.success("✅ Sesión de servidor cerrada")
        time.sleep(1)
        st.rerun()

else:
    st.sidebar.info("📱 Modo CLIENTE - Solo lectura")
    st.sidebar.markdown("Conectado al servidor para ver datos en tiempo real")
    st.sidebar.warning("🔒 **Modo de solo lectura**")

status_color = "🟢" if is_reading else "🔴"
st.sidebar.markdown(f"**Estado:** {status_color} {status_text}")

if is_reading:
    last_update = datetime.fromtimestamp(realtime_data['last_update'])
    time_diff = (datetime.now() - last_update).total_seconds()
    
    if time_diff < 5:
        st.sidebar.success(f"⏱️ Actualizado hace {time_diff:.1f}s")
    else:
        st.sidebar.warning(f"⚠️ Sin actualización ({time_diff:.0f}s)")

st.sidebar.markdown("---")

# =================================================================
# PESTAÑAS PRINCIPALES
# =================================================================


tab1, tab2, tab3 = st.tabs(["📊 Pesaje Actual", "📦 Historial", "🚚 Expediciones"])

with tab1:
    peso_display = current_peso
    st.markdown(f"""
    <div class="big-weight">
        {peso_display:.2f} kg
    </div>
    """, unsafe_allow_html=True)
    
    if is_reading:
        st.success("✅ Balanza activa - Datos compartidos en tiempo real para todos los usuarios")
    else:
        st.warning("⚠️ Balanza detenida - Actívala desde el modo SERVIDOR")
    
    st.markdown("---")
    
    
    
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("🧮 Cálculo de Peso Neto")
        
        if not st.session_state.is_server or not st.session_state.authenticated:
            st.info("📊 **Modo Visualización** - Los cálculos son solo de referencia")
        
        producto = st.selectbox(
            "Producto",
            options=list(PRODUCT_TO_WEIGHT.keys()),
            index=list(PRODUCT_TO_WEIGHT.keys()).index(st.session_state.last_product) 
                  if st.session_state.last_product in PRODUCT_TO_WEIGHT else 0,
            disabled=(not st.session_state.is_server or not st.session_state.authenticated)
        )
        
        col_a, col_b = st.columns(2)
        with col_a:
            cajas = st.number_input("Cantidad de Cajas", min_value=0, value=0, step=1,
                                   disabled=(not st.session_state.is_server or not st.session_state.authenticated))
            pallet = st.number_input("Peso del Pallet (kg)", min_value=0.0, value=0.0, step=0.1,
                                    disabled=(not st.session_state.is_server or not st.session_state.authenticated))
        
        with col_b:
            bandeja = st.selectbox("Tipo de Bandeja", list(TRAY_WEIGHTS.keys()),
                                  disabled=(not st.session_state.is_server or not st.session_state.authenticated))
            cant_bandejas = st.number_input("Cantidad de Bandejas", min_value=0, value=0, step=1,
                                           disabled=(not st.session_state.is_server or not st.session_state.authenticated))
        
        # Nuevos campos: Número de lote y Cantidad de hormas
        col_c, col_d = st.columns(2)
        with col_c:
            lote = st.text_input("Número de Lote", value="",
                                 disabled=(not st.session_state.is_server or not st.session_state.authenticated))
        with col_d:
            hormas = st.number_input("Cantidad de Hormas", min_value=0, value=200, step=1,
                                     disabled=(not st.session_state.is_server or not st.session_state.authenticated))
        
        # Cálculos
        peso_caja = PRODUCT_TO_WEIGHT[producto]
        peso_cajas = cajas * peso_caja
        peso_bandejas = cant_bandejas * TRAY_WEIGHTS[bandeja]
        peso_bruto = peso_display
        peso_neto = peso_bruto - pallet - peso_cajas - peso_bandejas
        
        # Mostrar desglose
        st.markdown("### 📋 Desglose del Cálculo")
        col_calc1, col_calc2, col_calc3, col_calc4 = st.columns(4)
        
        with col_calc1:
            st.metric("Peso Bruto", f"{peso_bruto:.2f} kg")
        with col_calc2:
            st.metric("Cajas", f"-{peso_cajas:.2f} kg", delta=f"{cajas} × {peso_caja}")
        with col_calc3:
            st.metric("Bandejas", f"-{peso_bandejas:.2f} kg", delta=f"{cant_bandejas} × {TRAY_WEIGHTS[bandeja]}")
        with col_calc4:
            st.metric("Pallet", f"-{pallet:.2f} kg")
        
        st.markdown("---")
        
        color = "green" if peso_neto >= 0 else "red"
        st.markdown(f"### ✅ **Peso Neto: <span style='color:{color}'>{peso_neto:.2f} kg</span>**", unsafe_allow_html=True)
        
        if st.session_state.is_server and st.session_state.authenticated:
            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                if st.button("💾 Guardar Registro", type="primary"):
                    if peso_bruto > 0:
                        entry = {
                            'producto': producto,
                            'cajas': cajas,
                            'bandeja': bandeja,
                            'cant_bandeja': cant_bandejas,
                            'pallet': pallet,
                            'bruto': peso_bruto,
                            'neto': peso_neto,
                            'lote': lote,
                            'hormas': hormas,
                            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        st.session_state.history_list.append(entry)
                        st.session_state.last_product = producto
                        save_config()
                        st.success(f"✅ Registro guardado: {peso_neto:.2f} kg")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.warning("⚠️ Peso bruto debe ser mayor a 0")
            
            with col_btn2:
                if st.button("🗑️ Limpiar Campos"):
                    st.rerun()
        else:
            st.warning("🔒 **Modo de solo lectura** - No se pueden guardar registros")
    
    with col2:
        st.subheader("📊 Estadísticas")
        total_neto = sum(item['neto'] for item in st.session_state.history_list)
        
        st.metric("Total Registros", len(st.session_state.history_list))
        st.metric("Total Neto Acumulado", f"{total_neto:.2f} kg")
        
        if st.session_state.history_list:
            promedio = total_neto / len(st.session_state.history_list)
            st.metric("Promedio por Pallet", f"{promedio:.2f} kg")
            
            ultimo = st.session_state.history_list[-1]
            st.markdown("---")
            st.markdown("**Último registro:**")
            st.text(f"{ultimo['producto']}")
            st.text(f"Neto: {ultimo['neto']:.2f} kg")

# Muestra el historial del día actual con opción de editar, borrar y archivar


with tab2:
    st.subheader("📦 Historial de Pallets Actual")

    if st.session_state.history_list:
        df = pd.DataFrame(st.session_state.history_list)
        df.index = range(1, len(df) + 1)
        df.index.name = '#'

        # Tabla bonita principal
        cols_to_show = ['producto', 'cajas', 'bandeja', 'cant_bandeja', 'pallet', 'bruto', 'neto', 'lote', 'hormas', 'timestamp']
        display_df = df[cols_to_show].copy()
        display_df.columns = ['Producto', 'Cajas', 'Bandeja', 'Cant.Band.', 'Pallet(kg)', 'Bruto(kg)', 'Neto(kg)', 'Lote', 'Hormas', 'Fecha/Hora']
        display_df['Neto(kg)'] = display_df['Neto(kg)'].apply(lambda x: f"**{x:.2f}**")
        display_df['Bruto(kg)'] = display_df['Bruto(kg)'].apply(lambda x: f"**{x:.2f}**")

        # Mostrar tabla limpia
        st.dataframe(
            display_df.style.set_properties(**{
                'text-align': 'center',
                'font-size': '14px'
            }).set_table_styles([
                {'selector': 'th', 'props': [('background-color', '#667eea'), ('color', 'white'), ('font-weight', 'bold')]},
                {'selector': 'td', 'props': [('border', '1px solid #ddd')]}
            ]),
            use_container_width=True,
            height=400
        )

        total_neto = df['neto'].sum()
        st.markdown(f"### 💰 **Total Neto Actual: {total_neto:.2f} kg**")

        # === SECCIÓN DE ACCIONES (solo servidor) ===
        if st.session_state.is_server and st.session_state.authenticated:
            st.markdown("### ✏️ Acciones sobre registros")
            
            # Mostrar todos los registros con botones de acción debajo
            for idx, reg in enumerate(st.session_state.history_list):
                # Si estamos editando este registro
                if st.session_state.get(f"editing_hist_{idx}", False):
                    with st.expander(f"✏️ Editando registro #{idx+1}: {reg['producto']} – {reg['neto']:.2f} kg", expanded=True):
                        mostrar_formulario_edicion(reg, idx, es_historial=True)
                    continue

                col1, col2, col3, col4 = st.columns([3, 2, 1.5, 1.5])
                with col1:
                    st.write(f"**#{idx+1}** – {reg['producto']}")
                    st.caption(f"Bruto: {reg['bruto']:.2f} kg | Neto: **{reg['neto']:.2f} kg** | Lote: {reg['lote'] or '-'} | Hormas: {reg['hormas']}")
                with col2:
                    st.caption(f"📦 {reg['cajas']} cajas | 🪣 {reg['cant_bandeja']} {reg['bandeja']} | ⚖️ Pallet: {reg['pallet']:.1f} kg")
                with col3:
                    if st.button("✏️ Editar", key=f"edit_hist_{idx}", use_container_width=True):
                        st.session_state[f"editing_hist_{idx}"] = True
                        st.rerun()
                with col4:
                    if st.button("🗑️ Borrar", key=f"del_hist_{idx}", type="secondary", use_container_width=True):
                        eliminar_registro(st.session_state.history_list, idx)

            st.markdown("---")

            # Botones globales
            col1, col2, col3 = st.columns(3)
            with col1:
                csv = display_df.to_csv(index=False, encoding='utf-8')
                st.download_button("📥 Exportar CSV", csv, f"historial_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", "text/csv")
            with col2:
                if st.button("🚚 Archivar → Expedición", type="primary", use_container_width=True):
                    today = datetime.now().strftime("%d/%m/%y")
                    today_expeditions = [e for e in st.session_state.expeditions if e['date'] == today]
                    next_num = len(today_expeditions) + 1
                    exp_name = f"{today} - Expedición {next_num}"
                    expedition = {
                        "date": today,
                        "name": exp_name,
                        "total": total_neto,
                        "records": st.session_state.history_list.copy()
                    }
                    st.session_state.expeditions.append(expedition)
                    st.session_state.history_list = []
                    save_config()
                    st.success(f"✅ Expedición creada: {exp_name}")
                    st.rerun()
            with col3:
                if st.button("🗑️ Limpiar Todo", type="secondary", use_container_width=True):
                    st.session_state.history_list = []
                    save_config()
                    st.rerun()

        else:
            st.info("🔒 Modo solo lectura – Conectate como servidor para editar")

    else:
        st.info("📭 No hay registros en el historial actual")
        


# Muestra todas las expediciones cerradas con filtros y edición

        
with tab3:
    st.subheader("🚚 Expediciones Archivadas")

    col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
    with col_f1:
        filter_prod = st.text_input("Filtrar por producto", "")
    with col_f2:
        filter_date = st.text_input("Filtrar por fecha (DD/MM/YY)", "")
    with col_f3:
        if st.button("Limpiar filtros"):
            st.rerun()

    if st.session_state.expeditions:
        filtered_exp = st.session_state.expeditions.copy()

        if filter_date:
            filtered_exp = [e for e in filtered_exp if filter_date in e['date']]
        if filter_prod:
            filtered_exp = [e for e in filtered_exp
                            if any(filter_prod.lower() in rec['producto'].lower()
                                   for rec in e['records'])]

        # Ordenar por fecha descendente (más nueva arriba)
        filtered_exp = sorted(filtered_exp, key=lambda x: x['date'], reverse=True)

        for i, exp in enumerate(filtered_exp):
            records = exp['records']
            if not records:
                continue

            # Todos los registros tienen el mismo producto (como es tu caso)
            producto = records[0]['producto']

            # Calcular totales
            total_kg = sum(r['neto'] for r in records)
            total_pallets = len(records)
            total_hormas = sum(r.get('hormas', 0) for r in records)

            # Nombre bonito para el expander
            nombre_expedicion = f"{exp['date']} – {producto}"

            with st.expander(
                f"{nombre_expedicion} → {total_kg:,.2f} kg | {total_pallets} pallets | {total_hormas:,} hormas",
                expanded=False
            ):
                # Resumen rápido
                st.markdown(f"**Producto:** {producto}")
                st.markdown(f"**Total:** {total_kg:,.2f} kg | **Pallets:** {total_pallets} | **Hormas:** {total_hormas:,}")

                # Tabla de detalle
                if records:
                    df_exp = pd.DataFrame(records)
                    df_exp.index = range(1, len(df_exp)+1)
                    df_disp = df_exp[['producto', 'cajas', 'bandeja', 'cant_bandeja', 'pallet', 'bruto', 'neto', 'lote', 'hormas']].copy()
                    df_disp.columns = ['Producto', 'Cajas', 'Bandeja', 'Cant.', 'Pallet', 'Bruto', 'Neto', 'Lote', 'Hormas']
                    df_disp['Neto'] = df_disp['Neto'].apply(lambda x: f"**{x:.2f}**")
                    df_disp['Bruto'] = df_disp['Bruto'].apply(lambda x: f"**{x:.2f}**")
                    st.dataframe(df_disp.style.set_properties(**{'text-align': 'center'}), use_container_width=True)

                # === Edición y eliminación (solo servidor) ===
                if st.session_state.is_server and st.session_state.authenticated:
                    st.markdown("### Editar registros")
                    for idx, reg in enumerate(records):
                        if st.session_state.get(f"editing_exp_{i}_{idx}", False):
                            with st.expander(f"Editando #{idx+1}: {reg['producto']}", expanded=True):
                                st.session_state.editing_exp_index = i
                                mostrar_formulario_edicion(reg, idx, es_historial=False)
                            continue

                        c1, c2, c3, c4 = st.columns([3, 2, 1.5, 1.5])
                        with c1:
                            st.write(f"**#{idx+1}** – {reg['producto']}")
                            st.caption(f"Neto: **{reg['neto']:.2f} kg** | Lote: {reg['lote'] or '-'} | Hormas: {reg['hormas']}")
                        with c2:
                            st.caption(f"Cajas: {reg['cajas']} | Bandejas: {reg['cant_bandeja']} | Pallet: {reg['pallet']:.1f} kg")
                        with c3:
                            if st.button("Editar", key=f"edit_e_{i}_{idx}", use_container_width=True):
                                st.session_state[f"editing_exp_{i}_{idx}"] = True
                                st.session_state.editing_exp_index = i
                                st.rerun()
                        with c4:
                            if st.button("Borrar", key=f"del_e_{i}_{idx}", type="secondary", use_container_width=True):
                                eliminar_registro(st.session_state.expeditions[i]['records'], idx)
                                # Recalcular totales
                                st.session_state.expeditions[i]['total'] = sum(r['neto'] for r in st.session_state.expeditions[i]['records'])
                                save_config()
                                st.rerun()

                    # Botones de exportar y eliminar expedición completa
                    c1, c2 = st.columns(2)
                    with c1:
                        if records:
                            csv = df_disp.to_csv(index=False, encoding='utf-8')
                            nombre_archivo = f"{exp['date'].replace('/', '-')}_{producto[:20]}.csv"
                            st.download_button("Exportar CSV", csv, nombre_archivo, "text/csv", key=f"dl_{i}")
                    with c2:
                        if st.button("Eliminar Expedición Completa", type="secondary", key=f"del_full_{i}"):
                            st.session_state.expeditions.remove(exp)
                            save_config()
                            st.rerun()
    else:
        st.info("Aún no hay expediciones archivadas")

# =================================================================
# AUTO-REFRESH CADA SEGUNDO
# =================================================================

time.sleep(1)
st.rerun()

# Footer
st.markdown("---")
col_footer1, col_footer2, col_footer3 = st.columns([2, 1, 1])
with col_footer1:
    st.markdown("**Sistema de Pesaje Industrial v2.0** | Multi-usuario | 🔒 Seguro")
with col_footer2:
    if st.session_state.is_server and st.session_state.authenticated:
        st.markdown("👥 Modo: 🖥️ Servidor (Autenticado)")
    else:
        st.markdown("👥 Modo: 📱 Cliente (Solo lectura)")
with col_footer3:
    st.markdown(f"🕐 {datetime.now().strftime('%H:%M:%S')}")