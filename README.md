# indAI MA

Versión 0.1 de un asistente web mínimo para el sector industrial, construido con Streamlit y acceso desacoplado al proveedor del LLM.

## Requisitos

- Python 3.10 o superior
- Una API key de OpenAI o un servidor local de LM Studio

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

3. Copia `.env.example` como `.env` y configura el proveedor.

   Para OpenAI:

   ```env
   LLM_PROVIDER=openai
   OPENAI_API_KEY=your_openai_api_key
   OPENAI_MODEL=gpt-4o-mini
   ```

   Para LM Studio, inicia su servidor local con el modelo cargado y utiliza:

   ```env
   LLM_PROVIDER=lmstudio
   LMSTUDIO_BASE_URL=http://localhost:1234/v1
   LMSTUDIO_MODEL=deepseek/deepseek-r1-0528-qwen3-8b
   ```

La sidebar permite cambiar el proveedor y el modelo durante la sesión sin modificar `.env`. Para LM Studio, los modelos de chat se consultan dinámicamente al servidor local y se excluyen los modelos de embeddings. Para OpenAI se muestra por ahora el modelo configurado en `OPENAI_MODEL`.

La interfaz de usuario obtiene la implementación configurada sin depender directamente del proveedor.

## Ejecución

```powershell
streamlit run app.py
```

La versión 0.1 procesa cada consulta de forma independiente y no conserva memoria conversacional.
