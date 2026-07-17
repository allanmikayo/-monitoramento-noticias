"""Ponto de entrada local: `python run.py` -> http://localhost:8765

Na primeira execução, rode antes: `python -m scripts.seed`
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.app:app", host="0.0.0.0", port=8765, reload=False)
