from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlmodel import Session, or_, select

from ..db import get_session
from ..models import Produto, Projeto
from ..util import D, UPLOADS, ler_3mf, page, paginate, salvar_upload

router = APIRouter()
EXT = {".stl", ".3mf", ".obj", ".step", ".stp", ".gcode", ".f3d", ".scad", ".zip"}


@router.get("/projetos")
def lista(request: Request, q: str = "", categoria: str = "", pagina: int = 1, s: Session = Depends(get_session)):
    st = select(Projeto).where(Projeto.ativo).order_by(Projeto.id.desc())
    if q:
        st = st.where(or_(Projeto.nome.ilike(f"%{q}%"), Projeto.descricao.ilike(f"%{q}%")))
    if categoria:
        st = st.where(Projeto.categoria == categoria)
    cats = [c for c in s.exec(select(Projeto.categoria).distinct()).all() if c]
    return page(request, "projetos.html", pg=paginate(s, st, pagina, 24), q=q, categoria=categoria, cats=cats,
                msg=request.query_params.get("msg", ""))


@router.post("/projetos")
async def enviar(request: Request, s: Session = Depends(get_session)):
    """Aceita vários arquivos de uma vez; cada um vira um projeto (nome = nome do arquivo)."""
    f = await request.form()
    criados, ignorados = [], 0
    for up in f.getlist("arquivos"):
        nome = getattr(up, "filename", "")
        if not nome or Path(nome).suffix.lower() not in EXT:
            ignorados += bool(nome)
            continue
        caminho = await salvar_upload(up, limite_mb=300)
        if not caminho:
            ignorados += 1
            continue
        peso, tempo = ler_3mf(UPLOADS / Path(caminho).name) if nome.lower().endswith(".3mf") else (D(0), 0)
        p = Projeto(nome=Path(nome).stem, arquivo=caminho, arquivo_nome=nome, categoria=f.get("categoria", "").strip(),
                    tamanho=(UPLOADS / Path(caminho).name).stat().st_size, peso_g=peso, tempo_min=tempo)
        s.add(p); criados.append(p)
    s.commit()
    msg = f"{len(criados)} projeto(s) enviado(s)" + (f"; {ignorados} ignorado(s) (tipo não aceito ou grande demais)" if ignorados else "")
    if len(criados) == 1:
        return RedirectResponse(f"/projetos/{criados[0].id}?msg={msg}", 303)
    return RedirectResponse(f"/projetos?msg={msg}", 303)


@router.get("/projetos/{id}")
def ver(id: int, request: Request, s: Session = Depends(get_session)):
    return page(request, "projeto.html", p=s.get(Projeto, id), msg=request.query_params.get("msg", ""),
                usos=s.exec(select(Produto).where(Produto.projeto_id == id)).all(),
                cats=[c for c in s.exec(select(Projeto.categoria).distinct()).all() if c])


@router.post("/projetos/{id}")
async def salvar(id: int, request: Request, s: Session = Depends(get_session)):
    f, p = await request.form(), s.get(Projeto, id)
    p.nome, p.descricao, p.categoria = f["nome"].strip(), f.get("descricao", "").strip(), f.get("categoria", "").strip()
    p.peso_g, p.tempo_min = D(f.get("peso_g")), int(D(f.get("tempo_min")))
    if foto := await salvar_upload(f.get("foto"), foto=True):
        p.foto = foto
    if novo := await salvar_upload(f.get("arquivo"), limite_mb=300):
        p.arquivo, p.arquivo_nome = novo, f["arquivo"].filename
        p.tamanho = (UPLOADS / Path(novo).name).stat().st_size
    s.add(p); s.commit()
    return RedirectResponse(f"/projetos/{id}?msg=Salvo", 303)


@router.get("/projetos/{id}/baixar")
def baixar(id: int, s: Session = Depends(get_session)):
    p = s.get(Projeto, id)
    caminho = UPLOADS / Path(p.arquivo).name
    if not p.arquivo or not caminho.exists():
        return RedirectResponse(f"/projetos/{id}?msg=Arquivo não encontrado", 303)
    return FileResponse(caminho, filename=p.arquivo_nome or caminho.name)


@router.post("/projetos/{id}/arquivar")
def arquivar(id: int, s: Session = Depends(get_session)):
    p = s.get(Projeto, id); p.ativo = False; s.add(p); s.commit()
    return RedirectResponse("/projetos?msg=Projeto arquivado (os produtos que o usam continuam com o arquivo)", 303)
