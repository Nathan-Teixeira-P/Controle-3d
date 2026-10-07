from decimal import Decimal

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import services as sv
from app.db import engine
from app.main import app
from app.models import Lancamento, MovEstoque, Pedido, Produto, Rolo, Tarefa, Venda

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
    c.post("/pedidos/1/mover", data={"para": "imprimindo", "confirmado": "1", "rolo": "1", "gramas": "100"})
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
        c.post("/pedidos/2/mover", data={"para": para, "confirmado": "1", **extra})
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
                            "produto_id": "1", "qtd": "1", "descricao": "", "preco": "60", "forcar": "1"})
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
    assert c.get("/calculadora").status_code == 200
    r = c.post("/produtos/calcular", data={"impressora_id": "1", "horas": "2", "min_trabalho": "0", "embalagem": "0", "acessorios": "0",
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


def test_lote_preco_sugerido_estoque_e_fornecedor(c):
    c.post("/estoque/compra", data={"tipo": "PLA", "cor": "Roxo", "peso": "1000", "preco": "100", "qtd": "1", "fornecedor": "Loja 3D"})
    c.post("/estoque/insumo", data={"nome": "Argola", "unidade": "un", "custo_unit": "0.5", "minimo": "0", "qtd": "100", "fornecedor": "Armarinho"})
    c.post("/config", data={"kwh": "1", "hora_trabalho": "0", "falhas_pct": "0", "margem_pct": "50", "custos_fixos_mes": "0", "horas_mes": "160"})
    c.post("/impressoras", data={"id": "1", "nome": "A1", "watts": "100", "valor": "4000", "vida_h": "4000", "manutencao_h": "0"})
    base = {"impressora_id": "1", "tempo_min": "600", "min_trabalho": "0", "embalagem": "0", "acessorios": "0", "margem_pct": "50",
            "mat_id": ["1"], "mat_g": ["200"], "ins_id": ["1"], "ins_q": ["10"], "lote_qtd": "10"}
    # lote de 10: filamento 200 g*0,10=20 + argolas 10*0,5=5 + energia 10h*0,1=1 + desgaste 10h*1=10 = 36 -> 3,60/un -> preço 7,20
    html = c.post("/produtos/calcular", data=base).text
    assert "R$ 36,00" in html and "R$ 3,60" in html and 'data-sug="7.20"' in html and "R$ 72,00" in html
    # salvar com estoque inicial, fornecedor e preço vindo do sugerido (campo vazio)
    c.post("/produtos", data={**base, "nome": "Chaveiro", "sku": "CH", "fornecedor": "Eu mesma", "preco": "", "preco_manual": "0"})
    with Session(engine) as s:
        p = s.exec(select(Produto).where(Produto.sku == "CH")).one()
        assert p.lote_qtd == 10 and p.fornecedor == "Eu mesma" and p.preco == D("7.20") and not p.preco_manual
        assert sv.saldo(s, "produto", p.id) == 0                              # cadastro não mexe no estoque
        assert sv.custo_produto(s, p)["custo"] == D("3.60")                  # custo por unidade do lote
        # consumo: gramas do produto são do lote (200 g/10 un): 3 peças = 60 g
        ped = Pedido(tipo="estoque"); s.add(ped); s.flush()
        from app.models import PedidoItem
        s.add(PedidoItem(pedido_id=ped.id, produto_id=p.id, descricao="x", qtd=3)); s.commit(); s.refresh(ped)
        assert sv.plano_consumo(s, ped)[0]["gramas"] == D("60")
    # editar só o estoque: grava a diferença como ajuste
    c.post("/produtos", data={**base, "id": "1", "nome": "Chaveiro", "sku": "CH", "preco": "9", "preco_manual": "1", "qtd_estoque": "20"})
    with Session(engine) as s:   # (mesmo mandando qtd_estoque, o produto ignora: estoque só pela página Estoque)
        assert sv.saldo(s, "produto", 1) == 0 and s.get(Produto, 1).preco == D("9") and s.get(Produto, 1).preco_manual
    assert "Loja 3D" in c.get("/estoque?aba=filamentos").text and "Armarinho" in c.get("/estoque?aba=insumos").text
    # calculadora com lote + insumo do estoque
    r = c.post("/produtos/calcular", data={"impressora_id": "1", "horas": "10", "lote_qtd": "10", "margem_pct": "50", "mat_id": ["1"], "mat_g": ["200"],
                                           "ins_id": ["1"], "ins_q": ["10"]})
    assert c.post("/calculadora", data={"impressora_id": "1", "horas": "10", "lote_qtd": "10", "margem_pct": "50", "mat_id": ["1"],
                                        "mat_g": ["200"]}).status_code == 200
    assert "R$ 36,00" in r.text and "R$ 3,60" in r.text and "R$ 7,20" in r.text


def test_tarefa_recorrente_nao_duplica_e_apagar_inexistente(c):
    c.post("/tarefas", data={"titulo": "Limpar mesa", "data": "2030-01-01", "hora": "", "recorrencia": "diaria"})
    for _ in range(3):                                   # marcar, desmarcar, marcar de novo
        assert c.post("/tarefas/1/toggle").status_code == 303
    with Session(engine) as s:
        ts = s.exec(select(Tarefa)).all()
        assert len(ts) == 2 and ts[0].feita and str(ts[1].data) == "2030-01-02"   # só UMA próxima
    c.post("/tarefas/1/toggle")                          # desmarcar remove a próxima, que ninguém tocou
    with Session(engine) as s:
        assert len(s.exec(select(Tarefa)).all()) == 1
    assert c.post("/tarefas/1/excluir").status_code == 303
    assert c.post("/tarefas/1/excluir").status_code == 303   # de novo (página desatualizada): não quebra
    assert c.post("/tarefas/99/toggle").status_code == 303


def _produto_lote(c):
    """Produto com lote 10: 200 g de filamento e 10 argolas por lote (= 20 g e 1 argola por unidade)."""
    c.post("/estoque/compra", data={"tipo": "PLA", "cor": "Roxo", "peso": "1000", "preco": "100", "qtd": "1"})
    c.post("/estoque/insumo", data={"nome": "Argola", "unidade": "un", "custo_unit": "0.5", "minimo": "0", "qtd": "100"})
    c.post("/produtos", data={"nome": "Chaveiro", "sku": "CH", "tempo_min": "600", "preco": "10", "lote_qtd": "10",
                              "mat_id": ["1"], "mat_g": ["200"], "ins_id": ["1"], "ins_q": ["10"], "impressora_id": "1"})


def test_pedido_abate_filamento_e_insumos_exatos(c):
    _produto_lote(c)
    c.post("/pedidos", data={"tipo": "cliente", "produto_id": "1", "qtd": "3", "descricao": "", "preco": ""})
    r = c.post("/pedidos/1/mover", data={"para": "imprimindo"})
    assert "confirmar" in r.headers["location"]                                  # mostra o que será gasto antes de baixar
    assert "Argola" in c.get("/pedidos/1?confirmar=1").text
    c.post("/pedidos/1/mover", data={"para": "imprimindo", "confirmado": "1", "rolo": "1", "gramas": "60", "ins_id": "1", "ins_q": "3"})
    with Session(engine) as s:
        assert sv.saldo(s, "rolo", 1) == D("940") and sv.saldo(s, "insumo", 1) == D("97")   # 3 un = 60 g e 3 argolas


def test_baixa_de_filamento_passa_para_outro_rolo(c):
    c.post("/estoque/compra", data={"tipo": "PLA", "cor": "Roxo", "peso": "1000", "preco": "100", "qtd": "2"})
    c.post("/estoque/rolo/1/ajuste", data={"restante": "30"})
    with Session(engine) as s:
        assert sv.baixar_rolo(s, 1, D("100"), "pedido", 1) == 0                   # 30 g do rolo 1 + 70 g do rolo 2
        s.commit()
        assert sv.saldo(s, "rolo", 1) == 0 and sv.saldo(s, "rolo", 2) == D("930")
        assert sv.baixar_rolo(s, 1, D("2000"), "pedido", 1) == D("1070")           # faltou: rolo escolhido fica negativo
        s.commit()
        assert sv.saldo(s, "rolo", 1) == D("-1070")


def test_venda_baixa_produto_pronto_e_nao_materia_prima(c):
    _produto_lote(c)
    c.post("/estoque/mov", data={"tipo": "produto", "item_id": "1", "delta": "4", "motivo": "ajuste"})
    r = c.post("/vendas", data={"data": "2030-01-01", "forma_pagamento": "PIX", "produto_id": "1", "qtd": "3", "descricao": "", "preco": ""})
    assert "msg" not in r.headers["location"]
    with Session(engine) as s:   # a venda só mexe no produto pronto; filamento e insumo ficam intactos
        assert sv.saldo(s, "produto", 1) == D("1") and sv.saldo(s, "rolo", 1) == D("1000") and sv.saldo(s, "insumo", 1) == D("100")
    r = c.post("/vendas", data={"data": "2030-01-01", "forma_pagamento": "PIX", "produto_id": "1", "qtd": "3", "descricao": "", "preco": ""})
    assert r.status_code == 200 and "ESTOQUE INSUFICIENTE" in r.text              # sem produto pronto suficiente: barreira
    r = c.post("/vendas", data={"data": "2030-01-01", "forma_pagamento": "PIX", "produto_id": "1", "qtd": "3", "descricao": "", "preco": "", "forcar": "1"})
    assert "negativo" in r.headers["location"]
    c.post("/vendas/1/estornar")                                                   # estorno devolve só o produto
    with Session(engine) as s:
        assert sv.saldo(s, "produto", 1) == D("1") and sv.saldo(s, "rolo", 1) == D("1000")


def test_cada_peca_abate_materia_prima_uma_vez(c):
    _produto_lote(c)                                                              # lote 10: 200 g e 10 argolas (20 g e 1 argola/un)
    # NÃO (imprimir): abate só filamento/insumo; nada passa pelo estoque pronto
    c.post("/pedidos", data={"tipo": "cliente", "do_estoque": "0", "produto_id": "1", "qtd": "5", "descricao": "", "preco": "10"})
    c.post("/pedidos/1/mover", data={"para": "imprimindo", "confirmado": "1", "rolo": "1", "gramas": "100", "ins_id": "1", "ins_q": "5"})
    for para in ("acabamento", "pronto", "enviado", "entregue"):
        c.post("/pedidos/1/mover", data={"para": para})
    with Session(engine) as s:
        assert sv.saldo(s, "rolo", 1) == D("900") and sv.saldo(s, "insumo", 1) == D("95")
        assert s.exec(select(MovEstoque).where(MovEstoque.item_tipo == "produto")).all() == []
    # produzir para estoque (abate 1x)
    c.post("/estoque/producao", data={"item_id": "1", "qtd": "10"})                  # -200 g, -10 argolas, +10 prontos
    # SIM (produto pronto): não imprime, não gasta material; entrega baixa só o produto pronto
    c.post("/pedidos", data={"tipo": "cliente", "do_estoque": "1", "produto_id": "1", "qtd": "4", "descricao": "", "preco": "10"})
    assert "confirmar" not in c.post("/pedidos/2/mover", data={"para": "imprimindo"}).headers["location"]
    for para in ("acabamento", "pronto", "enviado", "entregue"):
        c.post("/pedidos/2/mover", data={"para": para})
    with Session(engine) as s:
        assert sv.saldo(s, "rolo", 1) == D("700") and sv.saldo(s, "insumo", 1) == D("85")        # só a produção para estoque abateu
        assert sv.saldo(s, "produto", 1) == 6
    # NÃO de novo, mesmo havendo 6 prontas: imprime e NÃO mexe no produto pronto
    c.post("/pedidos", data={"tipo": "cliente", "do_estoque": "0", "produto_id": "1", "qtd": "3", "descricao": "", "preco": "10"})
    c.post("/pedidos/3/mover", data={"para": "imprimindo", "confirmado": "1", "rolo": "1", "gramas": "60", "ins_id": "1", "ins_q": "3"})
    for para in ("acabamento", "pronto", "enviado", "entregue"):
        c.post("/pedidos/3/mover", data={"para": para})
    with Session(engine) as s:
        assert sv.saldo(s, "rolo", 1) == D("640") and sv.saldo(s, "insumo", 1) == D("82") and sv.saldo(s, "produto", 1) == 6
    # a escolha pode mudar enquanto aguarda
    c.post("/pedidos", data={"tipo": "cliente", "do_estoque": "0", "produto_id": "1", "qtd": "1", "descricao": "", "preco": "10"})
    c.post("/pedidos/4/atendimento", data={"do_estoque": "1"})
    with Session(engine) as s:
        assert s.get(Pedido, 4).do_estoque


def test_barreira_de_estoque_insuficiente(c):
    _produto_lote(c)                                               # nada pronto; 1000 g de filamento; 100 argolas
    # --- venda de balcão sem produto pronto: barreira; "continuar" deixa negativo
    dados = {"data": "2030-01-01", "forma_pagamento": "PIX", "produto_id": "1", "qtd": "3", "descricao": "", "preco": "10"}
    r = c.post("/vendas", data=dados)
    assert r.status_code == 200 and "ESTOQUE INSUFICIENTE" in r.text and "faltam 3" in r.text and 'name="forcar"' in r.text
    with Session(engine) as s:
        assert not s.exec(select(Venda)).all() and sv.saldo(s, "produto", 1) == 0     # nada foi gravado
    r = c.post("/vendas", data={**dados, "forcar": "1"})
    assert r.status_code == 303 and "negativo" in r.headers["location"]
    with Session(engine) as s:
        assert sv.saldo(s, "produto", 1) == D("-3")
    # --- produzir sem material: 10 un. = 200 g; só há 1000 g -> ok; 60 un. = 1200 g -> barreira
    assert c.post("/estoque/producao", data={"item_id": "1", "qtd": "10"}).status_code == 303
    r = c.post("/estoque/producao", data={"item_id": "1", "qtd": "60"})
    assert "ESTOQUE INSUFICIENTE" in r.text and "Filamento" in r.text
    c.post("/estoque/producao", data={"item_id": "1", "qtd": "60", "forcar": "1"})
    with Session(engine) as s:
        assert sv.saldo(s, "rolo", 1) == D("-400")                                    # 1000 - 200 - 1200 (negativo)
        assert sv.saldo(s, "insumo", 1) == D("30")                                    # 100 - 10 - 60 argolas
    # --- pedido que vai usar produto pronto sem ter: barreira ao iniciar; imprimir sem filamento: barreira no confirmar
    c.post("/pedidos", data={"tipo": "cliente", "do_estoque": "1", "produto_id": "1", "qtd": "999", "descricao": "", "preco": "10"})
    r = c.post("/pedidos/1/mover", data={"para": "imprimindo"})
    assert "ESTOQUE INSUFICIENTE" in r.text and "Produto" in r.text
    c.post("/pedidos", data={"tipo": "cliente", "do_estoque": "0", "produto_id": "1", "qtd": "30", "descricao": "", "preco": "10"})
    r = c.post("/pedidos/2/mover", data={"para": "imprimindo", "confirmado": "1", "rolo": "1", "gramas": "600"})
    assert "ESTOQUE INSUFICIENTE" in r.text and "Filamento" in r.text
    with Session(engine) as s:
        assert s.get(Pedido, 2).status == "aguardando"                                # a barreira não deixou andar
    r = c.post("/pedidos/2/mover", data={"para": "imprimindo", "confirmado": "1", "rolo": "1", "gramas": "600", "forcar": "1"})
    assert r.status_code == 303
    with Session(engine) as s:
        assert s.get(Pedido, 2).status == "imprimindo"


def test_preco_sugerido_acompanha_qualquer_mudanca(c):
    c.post("/estoque/compra", data={"tipo": "PLA", "cor": "Roxo", "peso": "1000", "preco": "100", "qtd": "1"})
    c.post("/config", data={"kwh": "1", "hora_trabalho": "0", "falhas_pct": "0", "margem_pct": "50", "custos_fixos_mes": "0", "horas_mes": "160"})
    c.post("/impressoras", data={"id": "1", "nome": "A1", "watts": "100", "valor": "4000", "vida_h": "4000", "manutencao_h": "0"})
    base = {"impressora_id": "1", "tempo_min": "120", "min_trabalho": "0", "embalagem": "0", "acessorios": "0", "margem_pct": "50",
            "mat_id": ["1"], "mat_g": ["100"], "lote_qtd": "1", "preco": "", "preco_manual": "0"}
    c.post("/produtos", data={**base, "nome": "Auto", "sku": "A"})                       # preço automático
    c.post("/produtos", data={**base, "nome": "Manual", "sku": "M", "preco": "99", "preco_manual": "1"})
    preco = lambda sku: [p.preco for p in _prods() if p.sku == sku][0]

    def _prods():
        with Session(engine) as s:
            return s.exec(select(Produto)).all()
    p0 = preco("A")                                   # (10 + 0,2 + 2) / 0,5 = 24,40
    assert p0 == D("24.40") and preco("M") == D("99")
    c.post("/config", data={"kwh": "5", "hora_trabalho": "0", "falhas_pct": "0", "margem_pct": "50", "custos_fixos_mes": "0", "horas_mes": "160"})
    c.get("/produtos"); assert preco("A") > p0 and preco("M") == D("99")                 # energia mudou -> preço mudou
    p1 = preco("A")
    c.post("/estoque/compra", data={"tipo": "PLA", "cor": "Roxo", "peso": "1000", "preco": "300", "qtd": "1"})
    c.get("/produtos"); assert preco("A") > p1                                              # filamento mais caro
    p2 = preco("A")
    c.post("/produtos", data={**base, "id": "1", "nome": "Auto", "sku": "A", "margem_pct": "80"})
    assert preco("A") > p2 and preco("M") == D("99")                                       # margem maior
    assert 'data-sug="' in c.post("/produtos/calcular", data=base).text                  # pré-visualização ao vivo


def test_registrar_producao_abate_filamento_e_insumo(c):
    _produto_lote(c)                                         # lote 10: 200 g e 10 argolas
    with Session(engine) as s:                               # cadastrar o produto não mexe no estoque
        assert sv.saldo(s, "produto", 1) == 0 and sv.saldo(s, "rolo", 1) == D("1000") and sv.saldo(s, "insumo", 1) == D("100")
    r = c.post("/estoque/producao", data={"item_id": "1", "qtd": "10"})                 # página Estoque: produzi 10
    assert "Abatido" in __import__("urllib.parse", fromlist=["unquote"]).unquote(r.headers["location"])
    with Session(engine) as s:
        assert sv.saldo(s, "produto", 1) == 10 and sv.saldo(s, "rolo", 1) == D("800") and sv.saldo(s, "insumo", 1) == D("90")
    c.post("/estoque/producao", data={"item_id": "1", "qtd": "5"})
    with Session(engine) as s:
        assert sv.saldo(s, "produto", 1) == 15 and sv.saldo(s, "rolo", 1) == D("700") and sv.saldo(s, "insumo", 1) == D("85")
    # ajuste (aumentar/diminuir) NÃO mexe em matéria-prima
    c.post("/estoque/mov", data={"tipo": "produto", "item_id": "1", "delta": "-3", "motivo": "perda"})
    c.post("/estoque/mov", data={"tipo": "produto", "item_id": "1", "delta": "1", "motivo": "ajuste"})
    with Session(engine) as s:
        assert sv.saldo(s, "produto", 1) == 13 and sv.saldo(s, "rolo", 1) == D("700") and sv.saldo(s, "insumo", 1) == D("85")


def test_ficha_do_produto_e_so_cadastro(c):
    c.post("/estoque/compra", data={"tipo": "PLA", "cor": "Roxo", "peso": "1000", "preco": "100", "qtd": "1"})
    html = c.get("/produtos/novo").text
    assert "qtd_estoque" not in html and "abater_producao" not in html           # sem campo de quantidade na ficha
    c.post("/produtos", data={"nome": "Robô", "sku": "R", "preco": "10", "lote_qtd": "10", "mat_id": ["1"], "mat_g": ["50"]})
    assert "alterar no Estoque" in c.get("/produtos/1").text
    c.post("/calculadora/salvar", data={"nome": "Lote", "horas": "1", "lote_qtd": "4", "preco": "5", "margem_pct": "40", "registrar_lote": "1",
                                        "mat_id": ["1"], "mat_g": ["40"], "mat_preco": [""], "mat_pesorolo": [""]})
    with Session(engine) as s:   # nada de estoque foi mexido pelos cadastros
        assert sv.saldo(s, "produto", 1) == 0 and sv.saldo(s, "produto", 2) == 0 and sv.saldo(s, "rolo", 1) == D("1000")
    assert "Registrar produção" in c.get("/estoque?aba=produtos").text