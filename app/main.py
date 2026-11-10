from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from . import services as sv
from .db import engine, get_session
from .models import Impressora, Tarefa
from .routes import cadastros, estoque, financeiro, ia, orcamentos, pedidos, produtos, projetos, relatorios, vendas
from .util import BASE, UPLOADS, page

app = FastAPI()
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")
for r in (cadastros, estoque, projetos, produtos, orcamentos, pedidos, vendas, financeiro, relatorios, ia):
    app.include_router(r.router)

@app.on_event("startup")
def seed():
    with Session(engine) as s:  # impressora da cliente (valores aproximados; ajustar em Configurações)
        if not s.exec(select(Impressora)).first():
            s.add(Impressora(nome="Bambu Lab A1 (AMS Lite)", watts=100, valor=4000, vida_h=5000, manutencao_h=0.5))
            s.commit()


@app.exception_handler(Exception)
async def erro_amigavel(request, exc):
    import logging
    logging.getLogger("uvicorn.error").exception("Erro em %s", request.url.path, exc_info=exc)
    return HTMLResponse('<meta charset="utf-8"><body style="font:16px system-ui;padding:40px"><h2>Algo deu errado 😕</h2>'
                        '<p>A página pode estar desatualizada ou o registro já foi apagado.</p>'
                        '<p><a href="/">Voltar ao início</a> · <a href="javascript:history.back()">Voltar</a></p>', status_code=500)


@app.get("/health")
def health():
    return "ok"


# ---- Dashboard + tarefas
@app.get("/")
def dashboard(request: Request, s: Session = Depends(get_session)):
    hoje = date.today()
    return page(request, "dashboard.html", hoje=hoje, d=sv.dashboard(s, hoje))


@app.post("/tarefas")
def tarefa_nova(titulo: str = Form(), data: date = Form(), hora: str = Form(""), recorrencia: str = Form(""),
                s: Session = Depends(get_session)):
    s.add(Tarefa(titulo=titulo, data=data, hora=hora, recorrencia=recorrencia)); s.commit()
    return RedirectResponse("/", 303)


@app.post("/tarefas/{id}/toggle")
def tarefa_toggle(id: int, s: Session = Depends(get_session)):
    if t := s.get(Tarefa, id):  # já pode ter sido apagada (página desatualizada)
        sv.concluir_tarefa(s, t)
    return RedirectResponse("/", 303)


@app.post("/tarefas/{id}/excluir")
def tarefa_excluir(id: int, s: Session = Depends(get_session)):
    if t := s.get(Tarefa, id):
        s.delete(t); s.commit()
    return RedirectResponse("/", 303)
