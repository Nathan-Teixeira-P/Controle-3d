import math
import os
import re
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace

from fastapi.templating import Jinja2Templates
from sqlmodel import Session, func, select

from .db import engine
from .empresa import EMPRESA

BASE = Path(__file__).parent
UPLOADS = Path(os.environ.get("UPLOADS", "/app/uploads"))
UPLOADS.mkdir(parents=True, exist_ok=True)

NAV = [
    ("Início", [("/", "Dashboard")]),
    ("Vender", [("/clientes", "Clientes"), ("/orcamentos", "Orçamentos"), ("/pedidos", "Pedidos"), ("/vendas", "Vendas")]),
    ("Produzir", [("/projetos", "Projetos"), ("/produtos", "Produtos"), ("/calculadora", "Calculadora"), ("/estoque", "Estoque")]),
    ("Dinheiro", [("/financeiro", "Financeiro"), ("/relatorios", "Relatórios")]),
    ("Sistema", [("/config", "Configurações"), ("/ia", "Assistente IA")]),
]


def D(v, default=0):
    """Texto/número → Decimal, aceitando vírgula."""
    try:
        v = str(v).strip().replace(",", ".")
        return Decimal(v) if v else Decimal(default)
    except InvalidOperation:
        return Decimal(default)


def brl(v):
    v = Decimal(v or 0)
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def dec(v, casas=2):
    return f"{Decimal(v or 0):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def alertas():
    """(acabando, acabou) para o menu e a faixa de aviso."""
    from .services import estoque_baixo
    with Session(engine) as s:
        al = estoque_baixo(s)
    return sum(a["status"] == "acabando" for a in al), sum(a["status"] == "acabou" for a in al)


tpl = Jinja2Templates(directory=BASE / "templates")
tpl.env.filters.update(brl=brl, dec=dec)
tpl.env.globals.update(empresa=EMPRESA, nav=NAV, alertas=alertas)


def page(request, name, **ctx):
    return tpl.TemplateResponse(request, name, ctx)


def paginate(s: Session, stmt, page_n=1, per=20):
    total = s.exec(select(func.count()).select_from(stmt.order_by(None).subquery())).one()
    pages = max(1, math.ceil(total / per))
    page_n = min(max(1, int(page_n or 1)), pages)
    items = s.exec(stmt.offset((page_n - 1) * per).limit(per)).all()
    return SimpleNamespace(items=items, page=page_n, pages=pages, total=total)


async def salvar_upload(f, foto=False, limite_mb=50):
    """Salva UploadFile em UPLOADS com nome aleatório; foto é reduzida a 800px. Retorna '/uploads/x' ou ''."""
    if not f or not getattr(f, "filename", ""):
        return ""
    dados = await f.read()
    if not dados or len(dados) > limite_mb * 1024 * 1024:
        return ""
    ext = re.sub(r"[^a-z0-9]", "", Path(f.filename).suffix.lower())[:8]
    nome = f"{uuid.uuid4().hex}.{'jpg' if foto else ext or 'bin'}"
    if foto:
        from io import BytesIO
        from PIL import Image
        try:
            im = Image.open(BytesIO(dados)).convert("RGB")
        except Exception:
            return ""
        im.thumbnail((800, 800))
        im.save(UPLOADS / nome, "JPEG", quality=85)
    else:
        (UPLOADS / nome).write_bytes(dados)
    return f"/uploads/{nome}"


def whatsapp_link(telefone, texto=""):
    from urllib.parse import quote
    n = re.sub(r"\D", "", telefone or "")
    if n and not n.startswith("55"):
        n = "55" + n
    return f"https://wa.me/{n}?text={quote(texto)}"


def ler_3mf(caminho):
    """(peso_g, tempo_min) de um .3mf já fatiado (Bambu/Orca). Melhor esforço; (0, 0) se não achar."""
    import zipfile
    try:
        with zipfile.ZipFile(caminho) as z:
            x = z.read("Metadata/slice_info.config").decode("utf-8", "ignore")
        peso = sum(float(v) for v in re.findall(r'used_g="([\d.]+)"', x))
        seg = re.search(r'key="prediction"\s+value="(\d+)"', x)
        return Decimal(str(round(peso, 2))), (int(seg.group(1)) // 60 if seg else 0)
    except Exception:
        return Decimal(0), 0


def barreira(request, faltas, action, form, voltar, acao_txt="continuar"):
    """Tela grande de alerta: falta estoque. 'Continuar' reenvia o mesmo formulário com forcar=1 (estoque fica negativo)."""
    campos = [(k, v) for k, v in form.multi_items() if isinstance(v, str) and k != "forcar"]
    return page(request, "barreira.html", faltas=faltas, action=action, campos=campos, voltar=voltar, acao_txt=acao_txt)
