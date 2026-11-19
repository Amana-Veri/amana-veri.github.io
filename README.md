# Catálogo de joias da revendedora

Página com as peças disponíveis (fotos, preços, filtros e pedido pelo WhatsApp), atualizada sozinha
a partir do app de revendedora. Um link fixo para mandar às clientes.

```
App da marca (API) ──login──> coletar.py ──> docs/index.html + docs/fotos/ ──> GitHub Pages (link fixo)
                         (roda 07h e 18h no GitHub Actions)
```

## O que vai para o catálogo (e o que nunca vai)

- Vai: código, descrição, preço, categoria, banho, aro, coleção, fotos e quantidade disponível.
- Nunca vai: peças vendidas ou no carrinho, nome/ID de cliente, nº de série, % de desconto.
- CPF e senha ficam só nos *Secrets* do GitHub. Não aparecem no código nem nos logs.
- Se o login falhar, a API mudar ou a lista vier vazia, o catálogo anterior continua no ar
  e o GitHub manda um e-mail avisando.

## Arquivos

| Arquivo | Para quê |
|---|---|
| `coletar.py` | login, busca do inventário, travas de segurança, grava `docs/index.html` |
| `build_catalogo.py` | filtra e agrupa as peças (só campos públicos) e monta o HTML |
| `fotos.py` | baixa e comprime as fotos (capa 500 px, detalhe 1200 px) |
| `template.html` | visual do catálogo |
| `materiais.json` | cópia de reserva das fotos com modelo, usada só se a chamada `material-divulgacao` falhar |
| `.github/workflows/atualizar-catalogo.yml` | agendamento e publicação |

## 1. Testar no computador (recomendado antes do GitHub)

1. Instale o Python: https://www.python.org/downloads/ (marque "Add python to PATH").
2. Abra o terminal na pasta do projeto e rode:
   ```
   py -m pip install pillow
   py coletar.py --saida docs --baixar-fotos --diagnostico
   ```
3. Ele pede CPF e senha (a senha não aparece ao digitar). Se tudo der certo, abra `docs\index.html`.
4. Se o inventário não for achado sozinho, rode antes: `set VQ_INVENTARIO=135136`

## 2. Publicar no GitHub

1. Crie uma conta em https://github.com e um repositório **público** (o GitHub Pages grátis exige isso;
   só o código e o catálogo ficam visíveis, nunca o login).
   Dica: o link fica `https://SEU-USUARIO.github.io/NOME-DO-REPOSITORIO/`, então escolha nomes apresentáveis.
2. Envie todos os arquivos desta pasta, **incluindo a pasta `.github`** (Add file > Upload files, arrastando a pasta inteira).
3. Em **Settings > Secrets and variables > Actions**:
   - aba *Secrets*: `VQ_CPF` e `VQ_SENHA`
   - aba *Variables*: `VQ_INVENTARIO` (ex.: `135136`), `VQ_NOME_LOJA` (ex.: `Joias da Fulana`),
     `VQ_WHATSAPP` (só números, ex.: `5519999999999`). Opcional: `VQ_SUBTITULO`.
4. Aba **Actions** > *Atualizar catálogo* > **Run workflow**. Em 1–2 minutos deve aparecer a pasta `docs/`.
5. **Settings > Pages** > *Deploy from a branch* > `main` e pasta `/docs` > Save.
   O link aparece nessa mesma tela em alguns minutos.

Pronto: o catálogo se atualiza às 07h e às 18h. Para atualizar na hora, use o **Run workflow**.

## Quando algo mudar

- **Chegou uma maleta nova e o catálogo não mudou:** o número do inventário mudou. Atualize a variável `VQ_INVENTARIO`.
- **E-mail de falha do GitHub:** abra a execução em *Actions*; a última linha começa com `ERRO:` e diz o motivo.
- **Trocar nome, WhatsApp ou texto:** edite as *Variables* e rode o workflow.
- **Mudar o visual:** edite `template.html`.
