from decimal import Decimal

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import services as sv
from app.db import engine
from app.main import app
from app.models import Lancamento, MovEstoque, Pedido, Produto, Rolo, Venda

D = Decimal


def setup_basico(c):
    c.post("/clientes", data={"nome": "Maria", "telefone": "62999999999"})
    c.post("/estoque/compra", data={"tipo": "pla", "cor": "Roxo", "marca": "X", "peso": "1000", "preco": "100", "qtd": "1"})
    c.post("/canais", data={"nome": "Shopee", "taxa_pct": "20", "taxa_fixa": "0"})
    r = c.post("/produtos", data={"nome": "Vaso", "sku": "V1", "tempo_min": "120", "preco": "60", "margem_pct": "40",
                                  "mat_id": "1", "mat_g": "50", "impressora_id": "1"})
    assert r.status_code == 303
    return c


def test_exige_login():
    with TestClient(app, follow_redirects=False) as anon:
        assert anon.get("/").headers["location"] in ("/login", "/setup")
        assert anon.get("/clientes").status_code == 303


def test_calculadora_e_custo_produto(c):
    setup_basico(c)
    with Session(engine) as s:
        p = s.get(Produto, 1)
        r = sv.custo_produto(s, p)
        assert r["filamento"] == D("5.00")          # 50 g * R$0,10/g
        assert r["custo"] > r["filamento"] and r["preco"] > r["custo"]
    assert c.post("/calculadora", data={"peso_g": "50", "horas": "2", "material_id": "1", "impressora_id": "1",
                                        "margem_pct": "40", "embalagem": "0", "acessorios": "0", "min_trabalho": "0"}).status_code == 200


def test_fluxo_completo_orcamento_ate_venda(c):
    setup_basico(c)
    c.post("/orcamentos", data={"cliente_id": "1", "canal_id": "1", "validade_dias": "7", "desconto": "10", "frete": "5",
                                "prazo": "5d", "pagamento": "PIX", "observacoes": "", "produto_id": "1", "descricao": "",
                                "personalizacao": "roxo", "qtd": "2", "preco": "", "custo": "0"})
    assert c.get("/orcamentos/1/pdf?download=1").content[:5] == b"%PDF-"
    assert c.post("/orcamentos/1/aprovar", data={"prazo": "2030-01-01"}).headers["location"] == "/pedidos/1"
    with Session(engine) as s:
        ped = s.get(Pedido, 1)
        assert ped.total == D("115")   # 2*60 - 10 + 5
        custo_congelado = ped.itens[0].custo_unit
        assert custo_congelado > 0
    # imprimir: sem rolos na requisição → pede confirmação; com rolo baixa filamento
    assert "confirmar" in c.post("/pedidos/1/mover", data={"para": "imprimindo"}).headers["location"]
    c.post("/pedidos/1/mover", data={"para": "imprimindo", "rolo": "1", "gramas": "100"})
    with Session(engine) as s:
        assert sv.saldo(s, "rolo", 1) == D("900")
    # pular etapa é inválido
    assert "Movimento" in c.post("/pedidos/1/mover", data={"para": "pronto"}).headers["location"]
    c.post("/pedidos/1/falha", data={"rolo": "1", "gramas": "20"})
    for para in ("acabamento", "pronto", "enviado", "entregue"):
        c.post("/pedidos/1/mover", data={"para": para})
    with Session(engine) as s:
        vs = s.exec(select(Venda)).all()
        assert len(vs) == 1
        v = vs[0]
        assert v.total == D("115") and v.taxa == D("23.00")        # 20% de 115
        assert v.custo == 2 * custo_congelado + D("2.00")          # + falha 20 g * 0,10
        assert v.lucro == v.total - v.taxa - v.custo
        assert sv.saldo(s, "rolo", 1) == D("880")
        rec = s.exec(select(Lancamento).where(Lancamento.venda_id == v.id)).one()
        assert rec.valor == D("92.00") and rec.pago_em
    # entregue é final; entregar de novo não duplica venda
    assert c.post("/pedidos/1/mover", data={"para": "enviado"}).status_code == 303
    with Session(engine) as s:
        assert len(s.exec(select(Venda)).all()) == 1
    # estorno cancela receita
    c.post("/vendas/1/estornar")
    with Session(engine) as s:
        assert s.get(Venda, 1).status == "estornada"
        assert s.exec(select(Lancamento).where(Lancamento.venda_id == 1)).one().cancelado


def test_cancelado_nao_gera_venda_e_reposicao_estoque(c):
    setup_basico(c)
    c.post("/pedidos", data={"tipo": "cliente", "cliente_id": "1", "produto_id": "1", "qtd": "1", "descricao": "", "preco": ""})
    c.post("/pedidos/1/mover", data={"para": "cancelado"})
    c.post("/pedidos", data={"tipo": "estoque", "produto_id": "1", "qtd": "3", "descricao": "", "preco": ""})
    for para, extra in (("imprimindo", {"rolo": "1", "gramas": "150"}), ("acabamento", {}), ("pronto", {})):
        c.post("/pedidos/2/mover", data={"para": para, **extra})
    with Session(engine) as s:
        assert s.get(Pedido, 1).status == "cancelado" and not s.exec(select(Venda)).all()
        assert sv.saldo(s, "produto", 1) == D("3")
    # venda de balcão baixa do estoque pronto
    c.post("/vendas", data={"data": "2030-01-01", "forma_pagamento": "PIX", "produto_id": "1", "qtd": "2", "descricao": "", "preco": ""})
    with Session(engine) as s:
        assert sv.saldo(s, "produto", 1) == D("1")


def test_dashboard_financeiro_relatorios(c):
    setup_basico(c)
    c.post("/vendas", data={"data": __import__("datetime").date.today().isoformat(), "forma_pagamento": "PIX",
                            "produto_id": "1", "qtd": "1", "descricao": "", "preco": "60"})
    c.post("/lancamentos", data={"tipo": "despesa", "categoria": "Aluguel", "natureza": "fixa", "valor": "10", "descricao": "x",
                                 "vencimento": __import__("datetime").date.today().isoformat(), "pago": "1", "recorrencia": "mensal"})
    with Session(engine) as s:
        d = sv.dashboard(s, __import__("datetime").date.today())
        assert d["faturamento"] == D("60") and d["despesas"] == D("10")
        assert d["lucro"] == d["faturamento"] - d["taxas"] - d["cmv"] - d["despesas"]
        assert len(s.exec(select(Lancamento).where(Lancamento.recorrencia == "mensal")).all()) == 2   # gerou o próximo mês
    for rota in ("/", "/financeiro", "/estoque", "/estoque?aba=insumos", "/estoque?aba=produtos", "/estoque?aba=movimentos",
                 "/produtos", "/produtos/1", "/produtos/novo", "/pedidos", "/pedidos/novo", "/vendas", "/vendas/nova",
                 "/clientes", "/clientes/1", "/config", "/calculadora", "/ia", "/projetos", "/orcamentos", "/orcamentos/novo"):
        assert c.get(rota).status_code == 200, rota
    for nome in ("vendas", "custos", "lucratividade", "estoque", "produtos", "clientes", "producao"):
        assert c.get(f"/relatorios/{nome}").status_code == 200, nome
        assert c.get(f"/relatorios/{nome}?fmt=csv").content.startswith(b"\xef\xbb\xbf"), nome
        assert c.get(f"/relatorios/{nome}?fmt=xlsx").content[:2] == b"PK", nome


def test_projetos_upload_e_uso(c):
    setup_basico(c)
    r = c.post("/projetos", files={"arquivos": ("peca.stl", b"solid x\nendsolid", "model/stl")})
    assert r.status_code == 303 and r.headers["location"].startswith("/projetos/1")
    assert c.get("/projetos/1/baixar").content == b"solid x\nendsolid"
    assert c.post("/projetos", files={"arquivos": ("virus.exe", b"x")}).status_code == 303  # ignorado
    c.post("/produtos", data={"nome": "Peça", "sku": "P1", "projeto_id": "1", "preco": "10"})
    assert "peca" in c.get("/projetos/1").text and "Peça" in c.get("/projetos/1").text
    assert c.get("/calculadora?projeto_id=1").status_code == 200


def test_estoque_baixo_e_importacao(c):
    c.post("/estoque/compra", data={"tipo": "PETG", "cor": "Preto", "peso": "1000", "preco": "90", "minimo_g": "1500"})
    with Session(engine) as s:
        a = sv.estoque_baixo(s)
        assert a[0]["nome"].startswith("PETG") and a[0]["status"] == "acabando"   # 1000 g <= mínimo 1500
    csv = "tipo;material;cor;marca;peso_g;preco\nfilamento;PLA;Azul;Y;1000;80\ninsumo;Ímã;;;;\n"
    c.post("/estoque/importar", files={"arquivo": ("e.csv", csv.encode("utf-8"))})
    with Session(engine) as s:
        assert len(s.exec(select(Rolo)).all()) == 2


def test_calculadora_varios_filamentos_e_rateio(c):
    c.post("/estoque/compra", data={"tipo": "PLA", "cor": "Roxo", "peso": "1000", "preco": "100", "qtd": "1"})   # R$0,10/g
    c.post("/config", data={"kwh": "1", "hora_trabalho": "0", "falhas_pct": "0", "margem_pct": "50",
                            "custos_fixos_mes": "1600", "horas_mes": "160"})                                       # R$10/h de rateio
    c.post("/impressoras", data={"id": "1", "nome": "A1", "watts": "100", "valor": "4000", "vida_h": "4000", "manutencao_h": "0"})
    r = c.post("/calculadora", data={"impressora_id": "1", "horas": "2", "min_trabalho": "0", "embalagem": "0", "acessorios": "0",
                                     "margem_pct": "50", "mat_id": ["1", ""], "mat_g": ["50", "100"], "mat_preco": ["", "80"],
                                     "mat_pesorolo": ["", "1000"], "out_desc": ["parafuso"], "out_valor": ["3"]})
    assert r.status_code == 200
    # filamento 50*0,10 + 100*0,08 = 13 ; energia 0,2 ; desgaste 2 ; rateio 20 ; outros 3 -> 38,20 ; preço = /0,5
    assert "R$ 13,00" in r.text and "R$ 20,00" in r.text and "R$ 38,20" in r.text and "R$ 76,40" in r.text
    c.post("/calculadora/salvar", data={"nome": "Combo", "impressora_id": "1", "horas": "2", "preco": "76.40", "margem_pct": "50",
                                        "mat_id": ["1", ""], "mat_g": ["50", "100"], "mat_preco": ["", "80"], "mat_pesorolo": ["", "1000"],
                                        "out_desc": ["parafuso"], "out_valor": ["3"]})
    with Session(engine) as s:
        p = s.exec(select(Produto).where(Produto.nome == "Combo")).one()
        assert len(p.materiais) == 1 and p.acessorios == D("3")      # manual não vira material; outros custos somam em acessórios


def test_alertas_acabando_e_acabou(c):
    setup_basico(c)                                                                  # rolo de 1000 g, nenhum mínimo
    c.post("/estoque/compra", data={"tipo": "PLA", "cor": "Roxo", "marca": "X", "peso": "1000", "preco": "100", "qtd": "0", "minimo_g": "300"})
    with Session(engine) as s:
        assert sv.estoque_baixo(s) == []                                              # 2000 g: ok
        sv.mov(s, "rolo", 1, -850, "ajuste"); sv.mov(s, "rolo", 2, -850, "ajuste"); s.commit()
        assert [a["status"] for a in sv.estoque_baixo(s)] == ["acabando"]            # 300 g <= mínimo 300
    c.post("/estoque/rolo/1/ajuste", data={"restante": "0"}); c.post("/estoque/rolo/2/ajuste", data={"restante": "0"})
    with Session(engine) as s:
        assert [a["status"] for a in sv.estoque_baixo(s)] == ["acabou"]
    assert "acabou" in c.get("/estoque?aba=alertas").text and "row-bad" in c.get("/estoque?aba=alertas").text
    assert "acabou" in c.get("/").text                                                # faixa de alerta global
    c.post("/estoque/mov", data={"tipo": "produto", "item_id": "1", "delta": "5", "motivo": "ajuste"})
    c.post("/estoque/mov", data={"tipo": "produto", "item_id": "1", "delta": "-5", "motivo": "ajuste"})
    with Session(engine) as s:
        assert any(a["tipo"] == "Produto pronto" and a["status"] == "acabou" for a in sv.estoque_baixo(s))
