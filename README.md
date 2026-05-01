# LiteLLM Chatbot (React + FastAPI + LangChain)

This project includes:

- A frontend chatbot UI built with React + Vite.
- A Python backend built with FastAPI + LangChain.
- LiteLLM integration through `langchain-openai` (OpenAI-compatible API).

## 1. Prerequisites

- Node.js 18+
- Python 3.10+
- A LiteLLM API key

## 2. Environment Setup

Add these variables to your `.env` file in the project root:

```env
LITELLM_API_KEY=your_litellm_api_key_here
LITELLM_PROXY_URL=http://localhost:4000
```

Optional backend settings:

```env
LITELLM_CHAT_MODEL=gemini/gemini-2.5-flash
SYSTEM_PROMPT=You are a concise, helpful assistant.
CORS_ORIGINS=http://localhost:5173
```

You can also copy and use `backend/.env.example` as a reference.

## 3. Install Backend Dependencies

Create a virtual environment, activate it, and install backend requirements:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

## 4. Run the Backend

```bash
uvicorn backend.main:app --reload --port 8000
```

Health check:

```bash
curl http://localhost:8000/api/health
```

## 5. Run the Frontend

In a separate terminal:

```bash
npm install
npm run dev
```

Open the URL shown by Vite (typically `http://localhost:5173`).

The frontend calls `/api/chat`, and Vite proxies that route to `http://localhost:8000`.

## API Endpoint

`POST /api/chat`

Request body:

```json
{
	"message": "Explain recursion simply",
	"history": [
		{ "role": "user", "content": "Hi" },
		{ "role": "assistant", "content": "Hello!" }
	]
}
```

Response body:

```json
{
	"answer": "Recursion is when a function calls itself..."
}
```
