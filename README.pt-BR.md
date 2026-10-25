# mcp-display-companion

Serviço que roda no computador e **executa os toques de um painel MCP Display** —
o painel é um celular Android mostrando uma página (o app MCP Display expõe
`show_view`, `update_view`, `read_events` e `set_display` via MCP em
`http://<celular>:7777/mcp`). Os botões dessa página chamam tools pelo nome, e
este serviço é quem transforma esses nomes em ações de verdade na máquina:
play/pause, volume, abrir pasta, abrir app, rodar um atalho. O guia do próprio
painel (`panel://guide`) é o contrato que este serviço segue.

**[Read in English →](README.md)**

![Page 0: slideshow](docs/screenshots/apresentacao.png)
![Page 1: clock, weather and now playing](docs/screenshots/painel-1.png)
![Page 2: shortcuts](docs/screenshots/painel-2.png)
![Page 3: settings](docs/screenshots/painel-3.png)
![Rest mode: black and white, no cover](docs/screenshots/descanso.png)

O painel é **dado, não instrução**: o serviço só executa o que está declarado em
`config.json`. Um toque carrega o nome de uma ação — nunca uma linha de comando.

## Por que isso existe

Se ninguém no computador estiver escutando, o toque vira um evento numa fila que
ninguém lê: a tela parece viva e nada acontece. Uma sessão de agente consegue ler
esses eventos por um tempo — e morre junto com a sessão, em silêncio. Este
serviço é a parte chata que fica de pé: publica a própria tela, escuta, age,
confirma e **diz em voz alta** (no log e no painel) quando não está lá.

Ele também sobreviveu ao próprio bug: uma versão iniciada de dentro de uma sessão
de agente funcionava alguns minutos e depois emudecia, porque o processo ia junto
com a árvore da sessão. Ver [docs/service.md](docs/service.md).

## Requisitos

- Python **3.11+**
- Um painel MCP Display acessível na rede (`http://<ip-do-celular>:7777/mcp`) e o
  **token** dele
- Windows para o conjunto completo: mídia, capa do álbum e navegação de pastas
  usam APIs do Windows (`winrt`, `pywin32`). Em macOS/Linux o serviço roda com o
  host genérico (abrir pastas/URLs/apps/comandos), mas transporte de mídia e
  metadados da faixa **não** estão implementados lá — o botão existe e responde
  falha.

## Começando

```bash
git clone <url-do-repositório> mcp-display-companion
cd mcp-display-companion

python -m pip install -r requirements-windows.txt   # Windows: mídia, capa, pastas
cp config.example.json config.json                  # ajuste url, ações, pastas
```

O token fica **fora do repositório** — um comando grava no lugar certo:

```bash
python -m mcp_display_companion --set-token SEU-TOKEN-AQUI
```

```bash
python -m mcp_display_companion --check        # valida a configuração e mostra um resumo
python -m mcp_display_companion --print-view   # imprime o HTML que será publicado
python -m mcp_display_companion                # roda o serviço (primeiro plano)
```

Depois instale como **serviço do sistema**, para sobreviver a terminal fechado,
sessão de agente encerrada e queda do processo (no Windows é uma tarefa no logon:
volta a cada login):

```powershell
# Windows (Tarefa Agendada; remove o atalho antigo da Inicializar e reinicia)
powershell -ExecutionPolicy Bypass -File scripts\install-service.ps1
```

```bash
# macOS / Linux (launchd / systemd --user)
./scripts/install-service.sh
```

## Configurando

`config.json` (fora do git) é o produto inteiro. `config.example.json` é um
exemplo que funciona; a referência completa está em
[docs/configuration.md](docs/configuration.md).

```json
{
  "panel": { "url": "http://192.168.1.50:7777/mcp", "subscribe": true },
  "live": {
    "weather": { "enabled": true, "latitude": -23.55, "longitude": -46.63, "label": "São Paulo" },
    "media":   { "enabled": true, "cover": true }
  },
  "actions": [
    { "id": "pc_midia_play_pause", "label": "Play/Pause", "type": "media",
      "command": "play_pause", "icon": "playpause", "page": 1, "featured": true },
    { "id": "pc_abrir_downloads", "label": "Downloads", "type": "folder",
      "path": "%USERPROFILE%/Downloads", "icon": "download", "color": "#38bdf8" }
  ]
}
```

- **`id`** é o nome da tool que a View chama no toque. Use prefixo `pc_`: numa
  tela mista, um nome que também exista na conexão vai para a conexão.
- **`page: 1` + `featured: true`** é o botão grande da primeira página (o banner
  de mídia, se a ação for `media`); o resto vai para a grade de atalhos.
- **Ícones** vêm de `mcp_display_companion/icons.py`; qualquer outro pode usar
  `icon_svg` com um desenho inline.
- Adicionar um botão = adicionar uma ação. Adicionar um *tipo novo* de ação = um
  método em `actions.py` (`_run_<tipo>`) mais uma chamada no `Host`.

Tipos: `media`, `key`, `volume`, `folder`, `app`, `url`, `command`, `display`,
`rest` — campos de cada um em [docs/actions.md](docs/actions.md).

### Página de apresentação

Jogue fotos na pasta **`slides/`** que vem no repositório (ou aponte
`slides.folder` para outra) e aparece uma **página à esquerda do relógio** em
loop, com a foto **em tela cheia** — ela é o fundo do painel inteiro, sem card e
sem moldura — e um crossfade suave. Sem imagem, sem página. O serviço recorta cada foto no formato do painel, comprime
e manda como `data:` URL — uma imagem por `interval_s`, preparada numa thread
para os botões nunca esperarem. Jogar foto nova na pasta aparece sem reiniciar
nada.

### Modo descanso (página 3)

O painel fica na parede: um interruptor na **página de ajustes** liga e desliga o
modo descanso. Ligado, depois de `rest.idle_s` segundos sem toque a tela vira a
mesma página inicial em **preto puro, texto branco e sem a capa do álbum** — os
pixels baratos de um OLED — e o primeiro toque acorda. A escolha fica guardada no
PC, então recarregar a tela não esquece. Escurecer também leva o **brilho do
aparelho ao mínimo** e o devolve no primeiro toque. Desligado é o padrão: a tela
fica sempre acesa.

## O que ele faz com cada evento

| Evento | Ação do serviço |
|---|---|
| `toque` de ação conhecida | executa e confirma com `update_view` (o botão fica verde sem recarregar) |
| `toque` de outra conexão (`destino_indisponivel`) ou nome desconhecido | registra e ignora — o botão não é nosso |
| `toque` mais velho que `loop.max_event_age_s` | ignora (abrir uma pasta de 20 minutos atrás é pior que não abrir) |
| `acordou` (a tela apagada foi tocada) | só registra; não mexe no brilho de novo |
| `lacuna: true` (o painel pode ter perdido evento) | não repete ação; republica os dados vivos |

O cursor fica em disco (`cursor.json`), então reiniciar não repete toques. A
primeira leitura depois de subir só guarda o cursor: a fila do painel pode ter
toques de horas atrás.

## Single instance / Instância única

Ele se recusa a subir uma segunda cópia (trava de arquivo do sistema): dois companheiros no mesmo painel brigam pela View e a tela pisca.

## Segurança

- O token nunca está no repositório, nunca vai para o log e nunca é impresso
  (o `--check` mostra uma versão mascarada). Ver
  [docs/configuration.md#token](docs/configuration.md#token).
- O serviço conhece exatamente as ações de `config.json`. Nada que vem do painel
  é avaliado.
- O serviço não pede confirmação para nada: botão configurado executa. Então
  **não configure** desligar, apagar ou gastar numa tela que outras pessoas
  alcançam — o painel é um controle remoto do **seu** computador.

## Idioma

Código, comentários, mensagens de log, chaves de configuração e documentação estão
em inglês. A **tela de exemplo** — o texto que aparece no painel, os rótulos do
`config.example.json` e os ids do template — está em português; edite
`mcp_display_companion/templates/painel.html` (e os rótulos) para traduzir.

## Desenvolvimento

```bash
python -m pip install pytest
python -m pytest -q          # sem rede, sem APIs do Windows: tudo com fakes
```

Estrutura:

```
mcp_display_companion/
  __main__.py    CLI: rodar, --check, --print-view
  config.py      configuração JSON + segredos, validação, expansão de caminho
  client.py      transporte MCP (JSON-RPC sobre HTTP, SSE subscribe, 404/429)
  events.py      decisão pura: qual evento vira ação (bem testado)
  actions.py     registro de ações; só os ids configurados rodam
  host.py        tudo que é do sistema (GSMTC/Shell COM no Windows, genérico)
  live.py        clima (open-meteo) e metadados de mídia, cada um no seu ritmo
  runtime.py     o laço: heartbeat, watchdog, cursor, publicar/atualizar, confirmar
  view.py        monta templates/painel.html a partir da configuração
  icons.py       símbolos SVG
templates/painel.html   a tela (CSS, relógio, gestos, aviso de PC offline)
service.py       supervisor: reinicia o serviço e registra por que ele caiu
scripts/         instaladores (Tarefa Agendada, launchd, systemd --user)
tests/           pytest
```

## Limites

- Mídia (faixa/capa) e navegação de pastas são do Windows; nos outros sistemas o
  caminho genérico abre pastas/URLs/apps e a mídia cai nas teclas.
- O polling de reserva é 0,7 s; com `subscribe` o painel empurra o evento e o
  toque chega à ação em ~40 ms.
- O painel limita `update_view`/`set_display` a 10 chamadas por 10 s; o serviço
  agrupa os dados vivos e pula o que não mudou.
- O clima vem do open-meteo (sem chave); falha é silenciosa e o cartão mantém o
  último valor.

## Licença

MIT — ver [LICENSE](LICENSE).
