import os
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import services as sv
from .auth import confere, hash_senha
from .db import engine, get_session
from .models import Impressora, Tarefa, Usuario
from .routes import cadastros, estoque, financeiro, ia, orcamentos, pedidos, produtos, projetos, relatorios, vendas
from .util import BASE, UPLOADS, page

app = FastAPI()
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")
for r in (cadastros, estoque, projetos, produtos, orcamentos, pedidos, vendas, financeiro, relatorios, ia):
    app.include_router(r.router)

LIVRES = ("/login", "/setup", "/health", "/static")


class Auth(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not request.url.path.startswith(LIVRES) and not request.session.get("uid"):
            return RedirectResponse("/login", 303)
        return await call_next(request)


app.add_middleware(Auth)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "dev"), max_age=60 * 60 * 24 * 30,
                   same_site="lax", https_only=False)


@app.on_event("startup")
def seed():
    with Session(engine) as s:  # impressora da cliente (valores aproximados; ajustar em Configurações)
        if not s.exec(select(Impressora)).first():
            s.add(Impressora(nome="Bambu Lab A1 (AMS Lite)", watts=100, valor=4000, vida_h=5000, manutencao_h=0.5))
            s.commit()


@app.get("/health")
def health():
    return "ok"


@app.get("/setup")
def setup_get(request: Request, s: Session = Depends(get_session)):
    if s.exec(select(Usuario)).first():
        return RedirectResponse("/login", 303)
    return page(request, "login.html", setup=True, erro="")


@app.post("/setup")
def setup_post(request: Request, nome: str = Form(), senha: str = Form(), s: Session = Depends(get_session)):
    if s.exec(select(Usuario)).first():
        return RedirectResponse("/login", 303)
    if len(senha) < 6:
        return page(request, "login.html", setup=True, erro="Senha com pelo menos 6 caracteres.")
    u = Usuario(nome=nome.strip(), senha_hash=hash_senha(senha))
    s.add(u); s.commit(); s.refresh(u)
    request.session["uid"] = u.id
    return RedirectResponse("/", 303)


@app.get("/login")
def login_get(request: Request, s: Session = Depends(get_session)):
    if not s.exec(select(Usuario)).first():
        return RedirectResponse("/setup", 303)
    return page(request, "login.html", setup=False, erro="")


@app.post("/login")
def login_post(request: Request, nome: str = Form(), senha: str = Form(), s: Session = Depends(get_session)):
    u = s.exec(select(Usuario).where(Usuario.nome == nome.strip())).first()
    if not u or not confere(senha, u.senha_hash):
        return page(request, "login.html", setup=False, erro="Usuário ou senha incorretos.")
    request.session["uid"] = u.id
    return RedirectResponse("/", 303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)


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
    sv.concluir_tarefa(s, s.get(Tarefa, id))
    return RedirectResponse("/", 303)


@app.post("/tarefas/{id}/excluir")
def tarefa_excluir(id: int, s: Session = Depends(get_session)):
    s.delete(s.get(Tarefa, id)); s.commit()
    return RedirectResponse("/", 303)
