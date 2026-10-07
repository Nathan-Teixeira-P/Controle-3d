from decimal import ROUND_HALF_UP, Decimal

D0 = Decimal(0)
q2 = lambda x: Decimal(x).quantize(Decimal("0.01"), ROUND_HALF_UP)


def calcular(cfg, imp, filamento, horas, embalagem=0, acessorios=0, min_trabalho=0,
             margem_pct=None, taxa_pct=0, taxa_fixa=0, lote=1):
    """cfg=Config, imp=Impressora|None, filamento=R$ de material. Retorna dict (Decimal, 2 casas).
    As entradas valem para o LOTE inteiro (lote = unidades produzidas de uma vez). Componentes (filamento…falhas),
    custo_lote e preco_lote são do lote; custo, preco, taxa e lucro são POR UNIDADE. preco=None se margem+taxa >= 100%."""
    D = Decimal
    lote = max(int(lote or 1), 1)
    horas, filamento = D(horas), D(filamento)
    margem_pct = cfg.margem_pct if margem_pct is None else D(margem_pct)
    taxa_pct, taxa_fixa = D(taxa_pct), D(taxa_fixa)
    energia = (imp.watts / 1000 * horas * cfg.kwh) if imp else D0
    deprec = horas * imp.custo_hora if imp else D0
    mao = D(min_trabalho) / 60 * cfg.hora_trabalho
    rateio = horas * cfg.custos_fixos_mes / cfg.horas_mes if cfg.horas_mes else D0
    base = filamento + energia + deprec + mao + rateio + D(embalagem) + D(acessorios)
    falhas = base * cfg.falhas_pct / 100
    custo = base + falhas  # do lote
    unit = custo / lote
    divisor = 1 - (margem_pct + taxa_pct) / 100
    preco = (unit + taxa_fixa) / divisor if divisor > 0 else None
    return dict(filamento=q2(filamento), energia=q2(energia), deprec=q2(deprec), mao=q2(mao), rateio=q2(rateio),
                embalagem=q2(embalagem), acessorios=q2(acessorios), falhas=q2(falhas), lote=lote, custo_lote=q2(custo), custo=q2(unit),
                preco_lote=q2(preco) * lote if preco else None,
                preco=q2(preco) if preco else None,
                taxa=q2(preco * taxa_pct / 100 + taxa_fixa) if preco else None,
                lucro=q2(preco * margem_pct / 100) if preco else None)


if __name__ == "__main__":
    from app.models import Config, Impressora
    c = Config(kwh=Decimal(1), falhas_pct=Decimal(10), margem_pct=Decimal(50))
    i = Impressora(nome="x", watts=Decimal(100), valor=Decimal(4000), vida_h=Decimal(4000))
    r = calcular(c, i, 10, 2)
    assert (r["energia"], r["deprec"], r["custo"], r["preco"]) == (Decimal("0.20"), Decimal("2.00"), Decimal("13.42"), Decimal("26.84")), r
    assert calcular(c, i, 10, 2, margem_pct=60, taxa_pct=40)["preco"] is None
    print("ok")
