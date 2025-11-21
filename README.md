# ⚖️ Sistema de Pesaje Industrial - Balanza Multiusuario

Sistema completo en Streamlit para pesaje de pallets de queso con balanza serial, cálculo automático de tara (cajas, bandejas, pallet), historial, expediciones y modo multiusuario (servidor + clientes).

## Características
- Lectura en tiempo real de balanza (formatos EL05 y COND)
- Modo servidor/cliente (varios operarios viendo el mismo peso)
- Cálculo automático de peso neto
- Edición y eliminación de registros
- Archivo de expediciones diarias
- Protección con contraseña en modo servidor
- Exportación CSV

## Balanzas soportadas
- EL05 (formato M000010)
- Formato COND estándar

## Instalación
```bash
pip install streamlit pandas plotly pyserial
streamlit run balanza.py
