# Monitor de notícias

Programa local (Windows e Linux) que busca notícias de tecnologia em vários sites, coloca no topo as
**vulnerabilidades críticas** que você precisa corrigir e mostra tudo num leitor próprio. Também envia um
resumo bonito por e-mail.

![Tela inicial](docs/inicio.png)

## O que ele faz

- **Coleta** 25 fontes de uma vez (RSS e APIs oficiais): CISA KEV, NVD, GitHub Advisories, Microsoft MSRC,
  CERT.br, BleepingComputer, The Hacker News, Ars Technica, Hacker News, Tecnoblog e outras.
- **Área de games separada**: notícias de jogos ficam num menu próprio (Destaques, Meus jogos, Para você,
  Favoritos), sem misturar com tecnologia. Vem com 29 fontes, 19 ligadas (IGN Brasil, The Enemy, Voxel, GameBlast,
  MeuPlayStation, IGN, GameSpot, Eurogamer, PC Gamer...), destaque para lançamentos, jogos grátis e eventos,
  curtidas que aprendem separado e um resumo de games num e-mail próprio.
- **Prioriza** cada notícia: falhas já exploradas (CISA KEV), CVSS 9 ou mais, termos como *zero-day* e
  *RCE*, e qualquer menção às tecnologias que você usa (PHP, MariaDB, Laravel, Node.js...).
- **Aprende seu gosto**: marque notícias com *Gostei* ou *Não curti* e a aba **Para você** passa a ordenar
  as novas pelo que você curtiu. Tudo calculado no seu computador.
- **Leitor próprio**: abas por assunto, busca, favoritos, atalhos de teclado, tema claro/escuro e leitura do
  artigo completo dentro do programa (sem anúncios nem scripts do site).
- **Agrupa** a mesma falha citada por vários sites ("Também citado em").
- **Relatório de erros**: mostra quais sites falharam, por quê e há quantas coletas, com botão para corrigir.
- **Fontes editáveis**: inclua, edite, desative, remova e teste sites pelo programa. Cole o endereço de um
  site e ele procura o feed sozinho; sites sem RSS podem ser lidos com seletores CSS.
- **E-mail**: resumo em HTML pelo Outlook, Microsoft 365, Gmail, Yahoo ou qualquer SMTP. No Outlook, com
  login moderno da Microsoft (OAuth2), sem guardar senha.
- **Afeta a sua versão?** Cadastre as versões que você usa (PHP 8.2.10, MariaDB 10.11...) e o programa
  consulta o NVD para dizer se cada CVE afeta a sua versão ou não.
- **Chance de exploração (EPSS)** ao lado do CVSS, e uma **página por CVE** com gravidade, correção, exploit
  público, versões afetadas e linha do tempo.
- **Filtros pessoais** para silenciar ou destacar termos, e **busca completa** (sem acento, por começo de
  palavra, `-palavra` para excluir).
- **Celular**: destaques por Telegram, Discord ou Microsoft Teams. **Alertas na área de trabalho** com o modo
  residente, que fica perto do relógio.
- **Saúde do programa**: diagnóstico com um clique, fontes "silenciosas" (respondem, mas pararam de trazer
  novidade), saúde de cada fonte, agendamento da coleta diária com aviso quando ela não roda, faxina do banco,
  verificação de atualização com conferência de SHA-256 e relatório para suporte.
- **Seguro por padrão**: o leitor só mostra HTML limpo (lista de permissões e Content-Security-Policy), a coleta
  não acessa endereços da sua rede interna, downloads têm limite de tamanho, links com domínio suspeito pedem
  confirmação e o programa confere as próprias bibliotecas na base OSV todo dia.
- **Backup**: exporte e importe fontes, configurações, curtidas e favoritos; importe listas OPML.

| Leitor (tema escuro) | Para você |
| --- | --- |
| ![Leitor](docs/leitor-escuro.png) | ![Para você](docs/para-voce.png) |

## Instalar no Windows

1. Abra a página de [versões](https://github.com/Nerqs/monitor-noticias/releases/latest).
2. Baixe **MonitorNoticias-instalador-windows.exe** e abra.
3. Siga o assistente. Ele cria o atalho na área de trabalho e no menu Iniciar, e o programa aparece em
   "Aplicativos instalados" para desinstalar.

Por padrão instala só para você, sem pedir senha de administrador; no assistente dá para escolher "para
todos os usuários" (Arquivos de Programas). Os dados ficam em `%LOCALAPPDATA%\MonitorNoticias` e não são
apagados ao desinstalar. Quem já usava a versão .zip não perde nada: na primeira vez que abre, a versão
instalada copia os dados da pasta `data` do .zip (e deixa um aviso `DADOS-MOVIDOS.txt` lá).

**Atualização automática:** ao abrir, o programa consulta o GitHub e, se houver versão nova, mostra um
aviso com **Atualizar agora**. Com um clique ele baixa, confere o SHA-256, fecha, instala por cima e abre
de novo. Na versão .zip o mesmo botão abre o instalador; no Linux ele troca o executável. Dá para desligar o
aviso em Configurações → Aparência e leitura.

## Baixar e usar (sem instalar nada)

1. Abra a página de [versões](https://github.com/Nerqs/monitor-noticias/releases/latest).
2. Baixe **MonitorNoticias-windows.zip** (Windows) ou **MonitorNoticias-linux.tar.gz** (Linux).
3. Descompacte numa pasta sua (por exemplo, Documentos) e abra **MonitorNoticias.exe**.

Não precisa de Python: ele já vem dentro do programa. Na primeira vez o Windows pode avisar
"O Windows protegeu o computador", porque o programa não tem assinatura digital. Clique em
**Mais informações** e depois em **Executar assim mesmo**.

Seus dados ficam na pasta `data`, ao lado do programa. Para atualizar, baixe a versão nova e copie a pasta
`data` para dentro dela.

No Linux, se a janela própria não abrir, o programa usa o navegador. Para ter a janela, instale
`python3-gi` e `gir1.2-webkit2-4.1` (Debian/Ubuntu).

## Rodar a partir do código (para quem tem Python)

Precisa do [Python 3.10 ou mais novo](https://www.python.org/downloads/).

```bash
git clone https://github.com/Nerqs/monitor-noticias.git
cd monitor-noticias
```

**Windows:** dê dois cliques em `iniciar.bat`. **Linux:** rode `./iniciar.sh`.
Na primeira vez o script cria um ambiente virtual (`.venv`) e instala as dependências.

## Uso

| Parâmetro | O que faz |
| --- | --- |
| (nenhum) | Abre o leitor |
| `--coletar` | Abre o leitor e já começa a coletar |
| `--so-coletar` | Coleta sem abrir janela |
| `--so-coletar --email` | Coleta e envia o resumo por e-mail |
| `--navegador` | Usa o navegador em vez da janela própria |
| `--diagnostico` | Confere ambiente, banco, internet, relógio, e-mail e agendamento |
| `--residente` | Fica perto do relógio e avisa quando surge uma falha crítica |

Funcionam com o executável (`MonitorNoticias.exe --coletar`), com `python run.py` e com os scripts
`iniciar.bat` / `iniciar.sh`. No Windows o executável não tem console: o resultado fica em `data/logs/monitor.log`.

**Atalhos no leitor:** `j`/`k` próxima/anterior, `g` gostei, `n` não curti, `s` favoritar,
`m` lida/não lida, `a` ler artigo aqui, `o` abrir no navegador.

### Rodar sozinho de manhã (opcional)

O jeito mais fácil: **Saúde → Coleta automática → Agendar coleta diária**. O programa cria a tarefa no
Agendador de Tarefas (Windows) ou no crontab (Linux) e, se num dia ela não rodar, avisa quando você abrir o
leitor. Para fazer à mão:

- **Windows:** Agendador de Tarefas → Criar tarefa básica → programa
  `C:\caminho\MonitorNoticias\MonitorNoticias.exe`, argumentos `--so-coletar --email`.
- **Linux:** `crontab -e` e adicione
  `0 7 * * 1-5 ~/MonitorNoticias/MonitorNoticias --so-coletar --email`.

## App Android

O mesmo programa roda no celular: coleta, prioridade, versões afetadas, EPSS, página do CVE, filtros, busca,
curtidas e envio para Telegram/Discord/Teams. A tela se adapta ao celular (menu lateral, lista e notícia em
telas separadas).

- **Instalar:** baixe `MonitorNoticias-android.apk` em Releases (ou no artefato do GitHub Actions), abra no
  celular e permita "instalar apps de fontes desconhecidas" para o navegador ou gerenciador de arquivos.
- **Coleta:** acontece quando você abre o app (dá para desligar em Configurações) e pelo botão de atualizar.
  O Android não deixa apps coletarem escondidos sem gastar bateria; para avisos na hora, deixe o envio para o
  Telegram ligado no computador.
- **Diferenças:** sem agendamento, sem modo de alertas na bandeja e sem verificação de atualização. O artigo
  completo usa uma extração mais simples (sem lxml). Senhas e tokens ficam num arquivo na pasta privada do app.
- **Gerar o .apk à mão:** `pip install briefcase` e `briefcase package android -p debug-apk` (o Briefcase baixa o
  Java e o SDK do Android sozinho).

## E-mail

Em **Configurações → E-mail**, escolha o provedor (os campos do servidor são preenchidos sozinhos),
informe seu e-mail, os destinatários e salve a senha. A senha fica no cofre do sistema (Gerenciador de
Credenciais no Windows, chaveiro no Linux), nunca em arquivo. Sem cofre disponível, defina a variável de
ambiente `MONITOR_SMTP_PASSWORD`.

Use **Enviar e-mail de teste** para conferir e **Pré-visualizar resumo** para ver como fica.

![E-mail](docs/email.png)

**Outlook / Hotmail:** a Microsoft vem desligando o login por senha simples (SMTP AUTH) em várias contas.
Se o teste falhar com erro de autenticação, ative a verificação em duas etapas e crie uma
[senha de aplicativo](https://account.live.com/proofs/AppPassword). Em contas de empresa (Microsoft 365),
o administrador precisa liberar o SMTP AUTH para a sua caixa. Se nada disso for possível, use o Gmail (com
[senha de app](https://myaccount.google.com/apppasswords)) ou outro provedor.

### Login moderno da Microsoft (OAuth2)

Recomendado para Outlook/Hotmail e Microsoft 365. O programa nunca vê sua senha. É preciso registrar um
aplicativo grátis no Azure, uma vez só:

1. Entre em [portal.azure.com](https://portal.azure.com) com sua conta Microsoft → **Microsoft Entra ID** →
   **Registros de aplicativo** → **Novo registro**.
2. Nome: `Monitor de notícias`. Tipos de conta: **Contas em qualquer diretório organizacional e contas
   Microsoft pessoais**. Deixe o URI de redirecionamento vazio e clique em **Registrar**.
3. Em **Autenticação**, ative **Permitir fluxos de clientes públicos** e salve.
4. Em **Permissões de API** → **Adicionar** → **APIs que minha organização usa** → procure
   `Office 365 Exchange Online` → **Permissões delegadas** → marque `SMTP.Send`.
5. Copie o **ID do aplicativo (cliente)** da página Visão geral.

No programa: **Configurações → E-mail → Forma de login: Conta Microsoft**, cole o ID, salve e clique em
**Conectar conta Microsoft**. Abra o endereço mostrado, digite o código e autorize. A autorização fica no
cofre do sistema.

## Celular (Telegram, Discord ou Teams)

Em **Configurações → Celular**:

- **Telegram:** fale com [@BotFather](https://t.me/BotFather), envie `/newbot` e copie o token. Mande um
  "oi" para o seu robô, cole o token no programa, clique em **Salvar no cofre** e depois em **Descobrir**.
- **Discord:** Configurações do canal → Integrações → Webhooks → Novo webhook → Copiar URL.
- **Teams:** no canal, ··· → Fluxos de trabalho → "Postar em um canal quando uma solicitação de webhook for
  recebida" e copie o endereço.

Marque **Enviar destaques após cada coleta**. Só vão as notícias críticas e altas, e cada uma uma vez só.

## Alertas na área de trabalho

`--residente` (ou **Saúde → Iniciar alertas com o sistema**) deixa um ícone perto do relógio. A cada hora
(ajustável em **Configurações → Alertas**) ele coleta as fontes de segurança e mostra uma notificação para cada
notícia crítica nova. Pelo ícone dá para abrir o leitor, coletar agora ou pausar os alertas.

## Fontes

Em **Fontes** você pode:

- **Adicionar**: cole o endereço do site e clique em *Procurar feed*. Se o site não tiver RSS, escolha
  *Página sem RSS* e informe os seletores CSS (use F12 → Inspecionar no navegador para achá-los).
- **Testar** antes de salvar: mostra quantas notícias vieram e alguns exemplos.
- **Editar, desativar ou remover** qualquer fonte.
- Ver a **saúde** de cada uma nas últimas 30 coletas: taxa de sucesso, tempo de resposta, notícias por dia
  e quantas você curtiu.

Uma fonte que responde normalmente mas passa 7 dias sem nada novo (21 para o CISA KEV) aparece como
**silenciosa** em **Erros de coleta**: costuma ser feed que mudou de endereço.

A lista fica em `data/sources.yaml` e também pode ser editada à mão.

### Chaves opcionais

- **NVD:** sem chave a API aceita poucas consultas por minuto. Uma chave gratuita
  ([pedir aqui](https://nvd.nist.gov/developers/request-an-api-key)) evita erros 403/429.
- **GitHub:** um token sem permissões extras aumenta o limite da API de advisories.

## Como a prioridade é calculada

Cada notícia ganha pontos e vira **Crítico** (70+), **Alto** (40+) ou normal:

| Sinal | Pontos |
| --- | --- |
| Está no catálogo de falhas exploradas da CISA (KEV) | +60 |
| CVSS 9 ou mais / 7 ou mais | +45 / +25 |
| Termo crítico (zero-day, RCE, actively exploited...) | +50 |
| Cita uma tecnologia sua numa notícia de segurança | +35 |
| Fonte de segurança | +20 |
| Termo de segurança (vulnerability, patch, malware...) | +12 |
| O CVE afeta a versão que você cadastrou | +40 |
| A versão que você cadastrou não é afetada | −25 |
| EPSS 50% ou mais / 10% ou mais | +30 / +15 |
| Termo que você mandou destacar | +25 |

Os termos e as tecnologias são editáveis em **Configurações**. Em **Para você**, a ordem vem de um modelo
Naive Bayes treinado com as suas curtidas (ativa a partir de 3 curtidas).

## Onde ficam os dados

Tudo fica na pasta `data/` ao lado do programa (ou na pasta definida em `MONITOR_DATA_DIR`):
`monitor.db` (notícias, curtidas, erros), `config.yaml`, `sources.yaml`, `logs/` (log com rodízio, sem senhas)
e `backups/`. Senhas e tokens ficam no cofre do sistema. Nada é enviado para fora além das consultas aos sites
e às bases públicas (NVD, EPSS, OSV), do e-mail e do chat que você configurar.

## Segurança

- O artigo completo passa por uma lista de permissões de tags e atributos; o leitor tem Content-Security-Policy
  sem scripts de terceiros e o artigo abre num quadro sem permissão para rodar scripts.
- A coleta recusa endereços da rede interna (localhost, 10.x, 192.168.x, 169.254.x...), inclusive em
  redirecionamentos. Libere em **Configurações → Coleta** se precisar monitorar a intranet.
- Respostas acima de 10 MB são cortadas e feeds com declaração de entidades XML são recusados.
- O servidor local só atende em 127.0.0.1, compara o token em tempo constante e confere o cabeçalho Host.
- Links com domínio parecido com sites conhecidos, com caracteres de outro alfabeto ou com IP direto pedem
  confirmação antes de abrir.

## Desenvolvimento

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest
```

Para gerar o executável: `pip install pyinstaller` e `python empacotar.py` (o resultado fica em `dist/`).
No Windows, com o [Inno Setup 6](https://jrsoftware.org/isinfo.php) instalado, sai também o instalador
(`instalador/MonitorNoticias.iss`).
O GitHub Actions faz isso a cada PR para Windows e Linux. Ao criar uma tag `v*`
(ex.: `git tag v0.2.0 && git push origin v0.2.0`), os pacotes são publicados em Releases junto com o
`SHA256SUMS.txt` que o botão **Verificar atualização** usa para conferir o download.

Estrutura:

```
monitor/
  fetchers.py     coletores (RSS, CISA KEV, NVD, GitHub, raspagem), limites e novas tentativas
  netguard.py     bloqueio de endereços da rede interna
  sanitize.py     limpeza do HTML dos artigos
  cveinfo.py      EPSS, NVD, comparação de versões e página do CVE
  health.py       saúde das fontes e fontes silenciosas
  diagnostics.py  diagnóstico do programa
  scheduler.py    agendamento da coleta e do modo residente
  resident.py     modo residente (bandeja e notificações)
  notify.py       Telegram, Discord e Teams
  oauth.py        login OAuth2 da Microsoft para o e-mail
  selfcheck.py    autoauditoria das bibliotecas (OSV)
  updates.py      atualização com SHA-256 e troca automática da versão
  backup.py       backup, importação e OPML
  maintenance.py  faxina do banco
  logsetup.py     log em arquivo e relatório para suporte
  linkcheck.py    avaliação de domínios suspeitos
  scoring.py      prioridade de cada notícia
  preferences.py  aprendizado com Gostei / Não curti
  pipeline.py     coleta completa em paralelo
  db.py           banco SQLite
  emailer.py      resumo por e-mail
  web.py          API local usada pelo leitor
  templates/      leitor e e-mail (HTML)
  static/         estilo e JavaScript do leitor
  defaults/       configuração e fontes iniciais
```
