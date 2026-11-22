# Caça Obra – aplicativo

Aplicativo de celular (PWA) que lista obras e licitações que podem comprar concreto,
filtradas por cidade + raio, com contato, fase, prioridade e fonte.

## Peças
- `app.html` – o aplicativo (fonte). `index.html` é gerado dele por `coletor/montar_site.py`.
- `dados/obras.json` – lista que o app mostra (gerada pelo coletor).
- `dados/obras_piloto.json` – 59 obras da pesquisa manual de Grande POA (22/11/2026).
- `dados/obras_manuais.json` – obras particulares achadas em pesquisas futuras (notícias, alvarás, construtoras).
- `coletor/coletar.py` – busca automática: PNCP (licitações de obra) + vencedor + telefone/e-mail pelo CNPJ (BrasilAPI).
- `coletor/regioes.json` – regiões que entram na atualização semanal.
- `.github/workflows/atualizar-obras.yml` – roda o coletor toda segunda às 6h e publica o site (GitHub Pages).

## Como colocar no ar (GitHub, grátis)
1. Criar conta em github.com e um repositório público chamado `caca-obra`.
2. Enviar todos os arquivos desta pasta (inclusive a pasta `.github`).
3. Em Settings > Pages, escolher "Source: GitHub Actions".
4. Em Actions, abrir "Atualizar obras" e clicar "Run workflow".
5. O endereço fica `https://SEU-USUARIO.github.io/caca-obra/`. No celular, abrir e "Adicionar à tela inicial".

Busca avulsa numa região: Actions > Atualizar obras > Run workflow, preencher cidade, UF e raio.
A região fica salva e passa a ser atualizada toda semana.

## Rodar no computador
`python3 coletor/coletar.py --cidade "Passo Fundo" --uf RS --raio 80` e depois `python3 coletor/montar_site.py`.
