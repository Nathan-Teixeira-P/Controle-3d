# SPEC — Controle 3D Conex Lab

Sistema de gestão para uma pequena empresa de impressão 3D. Roda **somente no computador local da cliente**, acessado pelo navegador (PC ou celular, na mesma rede). Sem nuvem.

## 1. Princípios
1. **Simples de operar**: uma pessoa usa, sem treinamento. Poucos cliques por tarefa.
2. **Dados nunca se perdem**: backup automático, nenhuma exclusão destrutiva de histórico.
3. **Um fluxo só**: Orçamento → Pedido → Produção → Entrega → Venda → Financeiro, sem digitar a mesma coisa duas vezes.
4. **Custo congelado**: preço de filamento/energia muda; pedidos e vendas antigos guardam o custo da época.
5. **Menos código**: server-rendered, poucas dependências, uma tecnologia por camada.

## 2. Stack (decidida)
| Camada | Escolha | Motivo |
|---|---|---|
| Backend + frontend | **FastAPI + Jinja2 + HTMX** (Python) | Uma linguagem, um container, sem build de JS. HTMX cobre kanban/atualizações parciais. |
| Banco | **PostgreSQL 16** (container) | Robusto, `pg_dump` simples. |
| ORM / migrações | **SQLModel + Alembic** | Alembic entra na Fase 0 (hoje usa `create_all`). |
| PDF | **WeasyPrint** (HTML→PDF) | Mesmo template do orçamento na tela. |
| Excel/CSV | `csv` (stdlib, UTF-8 com BOM) + `openpyxl` para .xlsx | Abre direto no Excel. |
| IA local | **Ollama** (container opcional, perfil `ia`) | Open source, API HTTP simples. |
| Infra | **docker compose**, `restart: unless-stopped`, git | Sobe sozinho com o PC. |
| Testes | **pytest** | Só nas regras de negócio (ver §9). |

Frontend em JS puro (React etc.) só se um módulo exigir interação que HTMX não cobre — hoje nenhum exige.

## 3. Estado atual
**Fases 0–6 implementadas** e cobertas por testes (`app/tests`, 7 testes: fluxo orçamento→pedido→venda, custo congelado, estoque, estorno, relatórios, projetos, login) e verificadas no Postgres (incl. backup/restauração).

Desvios em relação ao desenho original (decididos na implementação):
- **Sem tabela `Categoria`**: categoria de produto/projeto/lançamento é texto livre com sugestões (YAGNI).
- **Projetos** (novo, pedido da cliente): aba para guardar STL/3MF/G-code/etc.; 3MF fatiado preenche peso e tempo; selecionável em Produtos, Calculadora e visível no Pedido para baixar.
- `Config` perdeu consumo/depreciação (agora por `Impressora`); ganhou CNPJ/endereço/PIX opcionais (saem no PDF só se preenchidos).
- Recorrência de tarefas (diária/semanal) e de lançamentos (mensal) geram a próxima ocorrência ao concluir/pagar.
- Kanban com botões ◀ ▶ (sem arrastar).
- Modelo de IA padrão `qwen2.5:1.5b` (PC da cliente: i7‑5500U, 12 GB, sem GPU).

## 4. Modelo de dados (alvo)
Valores monetários: `Numeric(12,2)`; peso em gramas `Numeric(10,2)`; tempo em minutos `int`. Toda tabela tem `id`, `criado_em`; as de cadastro têm `ativo` (arquivar em vez de excluir).

**Cadastros**
- `Config` (linha única): kWh, valor/hora de trabalho, % falhas, margem padrão.
- `Impressora`: nome, consumo W, valor, vida útil h, horas acumuladas, custo/h de manutenção. A depreciação vem daqui (substitui os campos hoje em `Config`).
- `Canal`: nome (Balcão, Shopee, Instagram, WhatsApp…), taxa %, taxa fixa R$.
- `Cliente`: nome, telefone, e-mail, CPF/CNPJ, endereço, observações.
- `Categoria` (de produto e de lançamento financeiro).

**Estoque**
- `Material`: tipo (PLA, PETG, ABS, TPU…), cor, marca. (Define "o que é" um filamento.)
- `Rolo`: material, peso inicial g, peso atual g, preço pago, `custo_por_g` (= preço ÷ peso inicial), comprado_em, status (`em_uso`/`novo`/`acabou`). **Cada rolo físico é um registro** — é o que dá custo/g exato e "peso restante".
- `Insumo`: nome, unidade, qtd atual, mínimo, custo unitário (embalagem, parafusos, ímãs…).
- `MovEstoque` (livro-razão): item (rolo/insumo/produto), delta, motivo (`compra`, `producao`, `falha`, `venda`, `ajuste`), referência (pedido/venda), data. **Quantidade atual = soma dos movimentos**; nunca editar saldo direto. Isso dá auditoria e "perdas/falhas" de graça.

**Produtos**
- `Produto`: nome, SKU único, categoria, foto, arquivo/modelo (upload ou caminho/URL), tempo médio min, impressora padrão, preço de venda, margem alvo, estoque pronto (via `MovEstoque`), mínimo.
- `ProdutoMaterial`: produto, material, gramas por unidade (um produto pode usar várias cores).
- `ProdutoInsumo`: produto, insumo, qtd.
- Custo e margem do produto são **calculados** (calculadora) sobre o rolo mais barato/atual do material — nunca digitados.

**Vendas**
- `Orcamento` / `Item` (já existem). Acrescentar: `produto_id` opcional no item (avulso continua possível), canal, status `aberto|aprovado|recusado|expirado`.
- `Pedido`: orçamento_id (opcional, venda direta cria pedido sem orçamento), cliente, canal, status, prazo de entrega, observações. Tipo `cliente` ou `estoque` (produzir para repor produto pronto).
- `PedidoItem`: produto/descrição, qtd, preço unit., **`custo_unit` congelado** na criação.
- `PedidoHistorico`: pedido, status anterior→novo, data (alimenta o kanban e relatórios de produção).
- `Venda`: pedido (ou avulsa), cliente, canal, forma de pagamento, data, total, custo total (soma dos congelados), taxa do canal, lucro. **Criada automaticamente ao entregar o pedido**, ou manualmente para venda de balcão.

**Financeiro**
- `Lancamento`: tipo (`receita`/`despesa`), categoria, natureza (`fixa`/`variavel`), descrição, valor, vencimento, pago_em (nulo = em aberto), `venda_id` opcional, `recorrencia` (mensal/nenhuma).
- Fluxo de caixa = lançamentos **pagos** por data; "a pagar/receber" = em aberto.

**Agenda**
- `Tarefa` (já existe): título, data, hora, feita. Acrescentar: `pedido_id` opcional e recorrência simples (diária/semanal). Prazos de entrega de pedidos aparecem na agenda automaticamente (sem criar tarefa).

**Sistema**
- `Usuario`: nome, hash de senha (scrypt da stdlib). Sessão por cookie assinado.

## 5. Regras de negócio
**Calculadora** (já implementada, evoluir):
`custo = (filamento + energia + depreciação + mão de obra + embalagem + acessórios) × (1 + %falhas)`
`preço = custo ÷ (1 − margem − taxa do canal)`.
Evolução: energia e depreciação usam a **impressora escolhida**; filamento usa o `custo_por_g` do rolo; botão "salvar como produto"; mostra preço por canal lado a lado.

**Orçamento → Pedido**: aprovar copia itens e congela `custo_unit` (calculado no momento). Orçamento passa a `aprovado` e fica vinculado. Orçamento vencido (`data + validade`) vira `expirado` na listagem.

**Pedido (máquina de estados)**: `aguardando → imprimindo → acabamento → pronto → enviado → entregue`; `cancelado` a partir de qualquer estado antes de `entregue`. Só avança/volta um passo por vez, exceto cancelar. Cada mudança grava `PedidoHistorico`.
- Ao entrar em **imprimindo**: pede o rolo e as gramas reais usadas (pré-preenchido com o previsto) → gera `MovEstoque(-g, producao)`; se o estoque do rolo for insuficiente, avisa mas não bloqueia.
- Botão "registrar falha": `MovEstoque(-g, falha)` e soma ao custo do pedido (alimenta o relatório de perdas).
- Ao entrar em **pronto**: se tipo `estoque`, `+qtd` em produto pronto.
- Ao entrar em **entregue**: gera `Venda` + `Lancamento(receita)`; se o produto vinha do estoque pronto, `−qtd`.
- **Cancelado**: não gera venda; filamento já consumido permanece como perda.

**Estoque baixo**: rolo/insumo/produto com saldo ≤ mínimo → badge no menu e card no dashboard.

**Dashboard (definições exatas, mês corrente)**
- *Faturamento* = Σ `Venda.total`.
- *Custos* = Σ `Venda.custo` (CMV) + Σ despesas pagas no mês.
- *Lucro* = Faturamento − taxas de canal − custos.
- *Vendas do mês* = nº de vendas. *Em produção* = pedidos em `imprimindo|acabamento`. *Mais vendidos* = top 5 por quantidade.
- Mais: tarefas de hoje, agenda 7 dias, pedidos com entrega atrasada.

## 6. Páginas e comportamento
Menu lateral agrupado (Início / Vender / Produzir / Dinheiro / Sistema). Layout responsivo (usável no celular). Listas têm busca e paginação; formulários validam no servidor e mostram erros inline.

| Página | Conteúdo / ações |
|---|---|
| Dashboard | §5. Tarefas com marcar/excluir, agenda. |
| Clientes | CRUD; ficha com orçamentos, pedidos e total comprado. |
| Orçamentos | CRUD; escolher produto (preenche preço) ou item avulso; **Aprovar → gera pedido**; PDF ver/baixar; duplicar; botão "enviar por WhatsApp" (link `wa.me` com texto pronto). |
| Pedidos | Kanban por status (arrastar ou botões). Card: cliente, itens, prazo (vermelho se atrasado). |
| Vendas | Lista filtrável; lançar venda avulsa; ver lucro por venda. |
| Produtos | CRUD com foto, materiais, insumos; custo/margem calculados e alerta de margem abaixo da meta. |
| Calculadora | Avulsa; "salvar como produto". |
| Estoque | Abas **Filamentos** (rolos, peso restante em barra, custo/g), **Insumos**, **Produtos prontos**. Ações: nova compra, ajuste, histórico de movimentos. |
| Financeiro | Lançamentos, contas a pagar/receber, fluxo de caixa mensal (gráfico simples), recorrentes geram o mês seguinte. |
| Relatórios | Vendas, custos, lucratividade (por produto/canal), estoque, produtos, clientes, produção (tempo/falhas). Filtro por período; **exportar CSV/XLSX**. |
| Configurações | Parâmetros, impressoras, canais, categorias, usuário/senha, backup manual, IA. |

**Documento do orçamento (PDF)** — mantém o layout atual: logo, número, emissão/validade, cliente, itens com personalização, subtotal/desconto/frete/total, prazo, pagamento, observações, termos, assinatura, rodapé `@conexlab3d` / WhatsApp. Dados da empresa (CNPJ, endereço, PIX) editáveis em Configurações e impressos se preenchidos.

## 7. IA local (opcional, última fase)
- Container Ollama, perfil `docker compose --profile ia up`. Modelo pequeno quantizado (ex.: classe 7–8B); **escolha final depende da RAM/GPU do PC dela — medir antes**. Sem GPU, usar modelo ≤ 3–4B ou deixar desligada.
- Casos de uso, em ordem de valor/risco:
  1. **Redigir** mensagem de orçamento/WhatsApp e descrição de produto (só gera texto; pessoa revisa).
  2. **Resumo do mês** em linguagem natural a partir de números já calculados pelo sistema.
  3. **Perguntas sobre os dados** ("quanto lucrei com chaveiros?") via *funções fixas* (a IA escolhe uma consulta pré-definida e os parâmetros). **Nunca** gerar SQL livre.
- A IA nunca altera dados sozinha; toda resposta é só sugestão. Se o Ollama estiver off, o resto do sistema funciona normalmente.

## 8. Requisitos não funcionais
- **Backup**: container `pg_dump` diário → `./backups`, retenção 30 dias, restore documentado e testado; botão "backup agora". Recomendar cópia periódica para pendrive/nuvem pessoal dela.
- **Segurança (rede local)**: login obrigatório; bind só na LAN (não expor à internet); senha com hash; cookie `HttpOnly`; escapar tudo nos templates (Jinja autoescape já ativo); validar uploads (tipo/tamanho, renomear, redimensionar foto com Pillow).
- **Acesso**: IP fixo ou hostname local (`controle.local`) documentado; atalho no celular.
- **Inicialização**: compose sobe com o sistema operacional; healthcheck do app.
- **Config**: segredos em `.env` (fora do git); `.env.example` versionado.
- **Dados**: fuso `America/Sao_Paulo`; formato BR (R$, dd/mm/aaaa); sem exclusão física de venda/pedido/lançamento (cancelar/estornar).
- **Desempenho**: irrelevante nessa escala; índices só em FKs e datas.

## 9. Testes (mínimo útil)
`pytest`, banco de teste descartável. Cobrir só o que envolve dinheiro/estoque:
1. Calculadora (preço, margem+taxa ≥ 100%).
2. Livro de estoque: saldo = soma dos movimentos; consumo ao imprimir; falha.
3. Fluxo Orçamento→Pedido→Entregue gera 1 venda, 1 receita, custo congelado, estoque correto; cancelado não gera venda.
4. Dashboard: totais batem com dados de exemplo.
5. PDF gera (`%PDF`) e contém número/total.
CI não é necessário; rodar `pytest` antes de cada commit.

## 10. Plano de entrega
Cada fase termina utilizável e com commit. Critério de pronto entre parênteses.

| Fase | Entrega | Pronto quando |
|---|---|---|
| **0 — Fundação** | Decimal, Alembic, login, `.env`, backup diário, editar/excluir cliente e orçamento, pytest base | Reiniciar o PC mantém tudo; restore de backup testado |
| **1 — Produzir** | Impressoras, Materiais/Rolos, Insumos, `MovEstoque`, Produtos com foto e custo calculado, calculadora integrada | Cadastrar um rolo e um produto e ver o custo/margem correto |
| **2 — Vender** | Canais, orçamento com produtos, aprovar→Pedido, kanban, histórico, consumo de filamento, Venda automática | Fluxo completo de ponta a ponta (§9.3) passando |
| **3 — Dinheiro** | Lançamentos, a pagar/receber, recorrentes, fluxo de caixa, dashboard completo, agenda com prazos | Números do dashboard batem com o financeiro |
| **4 — Relatórios** | 7 relatórios + export CSV/XLSX, alertas de estoque baixo | Exporta e abre no Excel sem quebrar acentos |
| **5 — Polimento** | Responsivo/celular, WhatsApp link, duplicar orçamento, busca, importar estoque inicial por CSV | Cliente usa no celular sem ajuda |
| **6 — IA** | Ollama + casos 1→3 (§7) | Gera mensagem de orçamento; desligável |

**Ordem de risco**: o que mexe com dinheiro e estoque (Fases 1–3) vem antes de qualquer conforto; IA por último.

## 11. Respostas da cliente
1. Usuários simultâneos: **1**. 2. Impressora: **Bambu Lab A1 + AMS Lite** (cadastrada por padrão: ~100 W, R$ 4.000, 5.000 h — ajustar em Configurações). 3. Estoque em planilha: **não** (importação CSV existe, opcional). 4. PDF com CNPJ/PIX: **não por enquanto**, mas editável em Configurações. 5. PC: **i7‑5500U, 12 GB RAM, sem GPU** (Windows) → IA com modelo ≤ 1.5B ou desligada.
Em aberto: taxas reais por canal (Shopee varia por faixa — modelar faixa só se ela precisar); valor real da impressora/consumo.

## 12. Fora de escopo (por ora)
Emissão de NF-e, integração automática com Shopee/marketplaces, controle de impressora via rede (OctoPrint), multiempresa, app nativo, acesso pela internet.
