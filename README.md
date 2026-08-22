# indAI MA

Versión 0.1 de un asistente web mínimo para el sector industrial, construido con Streamlit y la API de OpenAI.

## Requisitos

- Python 3.10 o superior
- Una API key de OpenAI

## Instalación

1. Crea y activa un entorno virtual:

   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

2. Instala las dependencias:

   ```powershell
   pip install -r requirements.txt
   ```

3. Copia `.env.example` como `.env` y sustituye el valor de ejemplo por tu API key:

   ```env
   OPENAI_API_KEY=your_openai_api_key
   ```

## Ejecución

```powershell
streamlit run app.py
```

La versión 0.1 procesa cada consulta de forma independiente y no conserva memoria conversacional.
