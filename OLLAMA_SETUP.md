# Ollama AI Copilot Setup Guide

## Prerequisites

1. **Install Ollama** (if not already installed):
   ```bash
   # macOS
   brew install ollama
   
   # Or download from https://ollama.ai
   ```

2. **Start Ollama service**:
   ```bash
   ollama serve
   ```

3. **Pull a model** (choose one):
   ```bash
   # Recommended for security analysis
   ollama pull llama3
   
   # Alternatives:
   ollama pull mistral
   ollama pull codellama
   ```

4. **Verify Ollama is running**:
   ```bash
   curl http://localhost:11434/api/tags
   ```

## Configuration

The application is pre-configured to use:
- **Ollama URL**: `http://localhost:11434` (or `http://host.docker.internal:11434` from Docker)
- **Default Model**: `llama3`

To change the model, update the environment variable:
```bash
# In docker-compose.yml or .env
OLLAMA_MODEL=mistral
```

## Using the AI Copilot

### 1. Case Analysis
- Open any case in the Case Detail view
- The AI will automatically analyze the case when you view it
- Analysis includes threat classification and recommended actions

### 2. Chat Assistant
- Click the AI Assistant icon in the sidebar
- Ask questions about the current case
- The assistant has full context of the case details

### 3. Example Prompts
- "What are the potential attack vectors for this incident?"
- "Suggest remediation steps for this security case"
- "Analyze the severity and priority of this threat"
- "What additional artifacts should I collect?"

## Troubleshooting

**Error: "Ollama service is not running"**
- Make sure Ollama is running: `ollama serve`
- Check if the service is accessible: `curl http://localhost:11434/api/tags`

**Error: "Model not found"**
- Pull the model: `ollama pull llama3`
- Verify with: `ollama list`

**Slow responses on first use**
- The model loads into memory on first request (can take 30-60 seconds)
- Subsequent requests will be much faster

**Docker connectivity issues**
- Ensure `host.docker.internal` is working
- On Linux, you may need to use `--network=host` or the actual host IP

## Model Recommendations

| Model | Size | Speed | Quality | Best For |
|-------|------|-------|---------|----------|
| llama3 | 4.7GB | Medium | High | General security analysis |
| mistral | 4.1GB | Fast | Good | Quick responses |
| codellama | 3.8GB | Fast | Medium | Code-related incidents |

## Performance Tips

1. **Keep Ollama running** - Avoid stopping/starting to keep model in memory
2. **Use smaller models** for faster responses if needed
3. **Increase timeout** in `ai_service.py` if using larger models
