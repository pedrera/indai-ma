# indAI MA

Versión 0.3 de un asistente web B2B multigás para los sectores sanitario e industrial, construido con Streamlit y acceso desacoplado al proveedor del LLM.

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

## Modos

- **Chat**: conversación con memoria durante la sesión activa.
- **Gas B2B Portfolio Analysis**: genera y valida con Pydantic un análisis estructurado de demanda, suministro, posiciones cortas y riesgos comerciales para gas natural, biometano, GNL y mezclas de hidrógeno.

El modo de análisis solicita JSON explícito al modelo y valida la respuesta antes de mostrarla. Una respuesta inválida genera un mensaje controlado y no detiene la aplicación.

Las generaciones se ejecutan fuera del hilo de interfaz. Mientras el modelo trabaja, la aplicación muestra su estado y permite interrumpir realmente la petición con **Detener generación**. El timeout total se configura en `.env`:

```env
LLM_TIMEOUT_SECONDS=300
```

Una cancelación o timeout conserva el historial anterior y no añade una respuesta incompleta.

## Ejecución

```powershell
streamlit run app.py
```

La versión 0.3 conserva el historial conversacional en la sesión activa de Streamlit. El botón **Nueva conversación** limpia solo ese historial. No existe persistencia entre sesiones ni se utiliza una base de datos.
