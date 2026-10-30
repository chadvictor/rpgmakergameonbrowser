<div align="center">

# 🎮 RPG Maker Launcher

### Seus jogos MV/MZ. Seu Linux. Um arquivo Python.

Abra jogos de RPG Maker em uma janela nativa com NW.js ou direto no navegador.

<p>
  <img alt="Python 3.8 ou superior" src="https://img.shields.io/badge/Python-3.8%2B-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white">
  <img alt="Runtime nativo para Linux" src="https://img.shields.io/badge/Nativo-Linux-FCC624?style=flat-square&amp;logo=linux&amp;logoColor=black">
  <img alt="RPG Maker MV e MZ" src="https://img.shields.io/badge/RPG_Maker-MV%20%2F%20MZ-8B5CF6?style=flat-square">
  <img alt="Biblioteca padrão do Python" src="https://img.shields.io/badge/Python-sem_depend%C3%AAncias_externas-22C55E?style=flat-square">
</p>

**[Começar](#-comece-por-aqui) · [Modos](#-dois-jeitos-de-jogar) · [Saves](#-seus-saves) · [Opções](#-opções) · [Compatibilidade](#-compatibilidade)**

</div>

---

## ✨ Do executável ao jogo

Muitos jogos de RPG Maker MV e MZ são distribuídos para Windows, mas usam JavaScript, HTML e NW.js por baixo do capô. Este launcher encontra esses arquivos e prepara a execução no Linux.

- **Um arquivo só.** Usa a biblioteca padrão do Python, sem `pip` ou `npm`.
- **NW.js automático.** Baixa o runtime oficial, verifica o SHA-256 e reutiliza o cache nas próximas execuções.
- **Plugins com APIs nativas.** O modo nativo oferece as APIs reais de Node/NW.js, como `fs` e `path`.
- **Arquivos japoneses.** Trata casos comuns de nomes CP932/Shift-JIS e diferenças entre maiúsculas e minúsculas no servidor local.
- **Pastas e pacotes.** Reconhece as estruturas usuais de MV/MZ, `.zip`, `.nw` e ZIP anexado ao executável NW.js.
- **Originais preservados.** O modo nativo prepara uma cópia; o modo navegador serve os arquivos sem alterá-los.

> O `Game.exe` funciona como ponto de partida para localizar o jogo. O executável de Windows não é iniciado.

## 🚀 Comece por aqui

### 1 · Baixe o script

Salve [`rpgmaker_browser.py`](rpgmaker_browser.py) em uma pasta da sua preferência. Você precisa de **Python 3.8 ou superior**.

### 2 · Aponte para o jogo

Para jogar em uma janela nativa no Linux:

```bash
python3 rpgmaker_browser.py "/home/voce/Jogos/Meu Jogo/Game.exe" --native
```

Também pode passar o ZIP completo:

```bash
python3 rpgmaker_browser.py "/home/voce/Downloads/Meu Jogo.zip" --native
```

### 3 · Jogue

Na primeira execução, o launcher baixa o NW.js oficial e prepara os arquivos. Depois, abre a janela do jogo. Nas próximas execuções, reutiliza o runtime e a cópia preparada quando os arquivos de origem permanecem iguais.

**Feche a janela ou pressione `Ctrl+C` no terminal para encerrar.**

<details>
<summary><strong>Requisitos do modo nativo</strong></summary>

- Linux com sessão gráfica.
- Espaço para o runtime e uma cópia dos arquivos do jogo.
- Internet para o primeiro download do runtime, ou um NW.js já instalado com `--nw-path`.
- Bibliotecas de sistema exigidas pela versão do NW.js escolhida.

O launcher usa o diretório do usuário e não exige instalação global do runtime. Se faltarem bibliotecas de sistema, a verificação com `ldd`, quando disponível, informa quais estão ausentes. A instalação desses pacotes fica a cargo do gerenciador de pacotes da sua distribuição.

</details>

## 🧭 Dois jeitos de jogar

| Modo | Como abrir | Quando escolher |
| :--- | :--- | :--- |
| **🖥️ Nativo · NW.js** | Adicione `--native` | Primeira escolha no Linux, especialmente para jogos com plugins que usam Node/NW.js. |
| **🌐 Navegador** | Execute sem `--native` | Jogos e plugins compatíveis com a execução web. |

### Abrir no navegador

```bash
python3 rpgmaker_browser.py "/home/voce/Jogos/Meu Jogo/Game.exe"
```

O launcher inicia um servidor em `127.0.0.1` e abre o navegador padrão. Mantenha o terminal aberto enquanto joga e clique no aviso para ativar o som. O navegador precisa oferecer os recursos gráficos e de áudio usados pelo jogo.

Nesse modo, ZIPs são lidos diretamente, sem extração. Também existe uma adaptação restrita à versão validada do **TDS Bitmap Fonts 1.6**, aplicada apenas à resposta enviada ao navegador. Outros plugins que dependem de Node/NW.js podem exigir o modo nativo ou uma adaptação própria.

## 📦 O que você pode passar

| Entrada | Exemplo |
| :--- | :--- |
| Executável do jogo | `"/home/voce/Jogos/Meu Jogo/Game.exe"` |
| Pasta completa | `"/home/voce/Jogos/Meu Jogo"` |
| Página de entrada | `"/home/voce/Jogos/Meu Jogo/www/index.html"` |
| Pacote ZIP | `"/home/voce/Downloads/Meu Jogo.zip"` |
| Pacote NW.js | `"/home/voce/Jogos/Meu Jogo/package.nw"` |

As estruturas mais comuns são:

```text
RPG Maker MV                    RPG Maker MZ
Meu Jogo/                       Meu Jogo/
├── Game.exe                    ├── Game.exe
├── package.json                ├── package.json
└── www/                        ├── index.html
    ├── index.html              ├── js/
    ├── js/                     ├── data/
    ├── data/                   └── ...
    └── ...
```

Passe a pasta completa ou o pacote original. Um `.exe` isolado só funciona quando contém os arquivos em um formato reconhecido pelo launcher.

## 💾 Seus saves

### No modo nativo

Os saves ficam separados das cópias preparadas e do cache do runtime:

```text
~/.local/share/rpgmaker-launcher/
└── <id-do-jogo>/
    ├── apps/             # Cópias preparadas do jogo
    ├── save/             # Saves da engine
    ├── chromium/         # Perfil do NW.js
    └── nw-version.txt    # Versão do runtime escolhida

~/.cache/rpgmaker-launcher/
└── runtimes/             # Runtimes baixados e verificados
```

Os caminhos respeitam `XDG_DATA_HOME` e `XDG_CACHE_HOME`. O launcher imprime a pasta dos saves ao iniciar.

O perfil é identificado pelo caminho de origem do jogo. **Mover a pasta ou o ZIP, ou alternar entre ZIP e pasta extraída, pode criar um perfil diferente.** Preserve a pasta `save/` ao fazer backup.

### No navegador

Os saves usam o armazenamento do próprio navegador. Para continuar acessando-os, use o mesmo navegador, o mesmo caminho de origem do jogo e a mesma porta.

Evite navegação privada e preserve os dados do site. Mudar a porta muda a origem usada pelo navegador para armazenar os saves.

> Saves do Windows e saves entre os dois modos não são migrados automaticamente.

## ⚙️ Opções

| Opção | O que faz |
| :--- | :--- |
| `--native` | Abre o jogo com NW.js nativo no Linux. |
| `--nw-version VERSÃO` | Escolhe uma versão do NW.js. Requer `--native`. |
| `--nw-path CAMINHO` | Usa um binário Linux `nw` existente, sem baixá-lo. Requer `--native`. |
| `--port PORTA` | Define a porta do servidor local. |
| `--zip-encoding ENCODING` | Define a codificação legada dos nomes. Padrão: `cp932`. |
| `--no-browser` | Inicia o modo navegador e imprime a URL sem abri-la automaticamente. |
| `--verbose` | Exibe todas as requisições HTTP. |
| `--help` | Mostra a ajuda completa. |

### Escolher uma versão do runtime

```bash
python3 rpgmaker_browser.py "/home/voce/Jogos/Meu Jogo" \
  --native --nw-version 0.119.2
```

Sem uma versão explícita, o primeiro download usa a versão estável anunciada pelo NW.js. A versão escolhida fica registrada no perfil do jogo e é reutilizada. Jogos antigos podem exigir outra versão compatível.

### Usar um NW.js que você já tem

```bash
python3 rpgmaker_browser.py "/home/voce/Jogos/Meu Jogo" \
  --native --nw-path "/home/voce/Apps/nwjs/nw"
```

### Abrir o navegador manualmente

```bash
python3 rpgmaker_browser.py "/home/voce/Jogos/Meu Jogo" \
  --no-browser --port 8123
```

Copie a URL exibida no terminal para o navegador. `--no-browser` é exclusivo desse modo e não pode ser combinado com `--native`.

## 🧩 Compatibilidade

O foco é **RPG Maker MV e MZ com seus arquivos JavaScript e recursos acessíveis**. A compatibilidade final depende dos plugins, do empacotamento e da versão do runtime.

| Caso | Situação |
| :--- | :--- |
| MV com pasta `www/` ou MZ com arquivos na raiz | Estruturas reconhecidas. |
| `.zip`, `.nw` e ZIP anexado ao executável NW.js | Formatos reconhecidos. |
| APIs padrão de Node/NW.js | Disponíveis no modo nativo. |
| Nomes japoneses e diferenças de maiúsculas/minúsculas | Tratamento de casos comuns no servidor local; não equivale a emular todo o sistema de arquivos do Windows. |
| Plugins com DLLs ou módulos nativos de Windows | Podem exigir uma versão Linux do componente. |
| Scripts `.bin` compilados pelo `nwjc` | Podem depender de versão e plataforma específicas. |
| Enigma Virtual Box e outros empacotadores | Exigem obter ou extrair os arquivos web por outros meios. |
| RPG Maker XP, VX, VX Ace, 2000 e 2003 | Fora do escopo deste launcher. |

O launcher preserva o idioma do jogo. O tratamento de nomes japoneses ajuda a localizar arquivos; ele não traduz o conteúdo nem emula o locale do Windows.

## 🛠️ Se algo não abrir

| Sintoma | Próximo passo |
| :--- | :--- |
| `Não achei um jogo MV/MZ` | Confira a engine e passe a pasta completa ou o ZIP original. Um instalador ou EXE empacotado pode precisar de extração prévia. |
| `require is not defined` no navegador | Tente `--native` no Linux. O plugin está tentando acessar uma API ausente no navegador. |
| Bibliotecas de sistema ausentes | Instale os pacotes correspondentes na sua distribuição e execute novamente. |
| Porta já está em uso | Encerre a outra instância ou escolha outra porta com `--port`. No navegador, lembre que os saves ficam associados à porta anterior. |
| NW.js abre, mas o jogo falha | Confira o erro no terminal e teste uma versão do runtime compatível com o jogo usando `--nw-version`. |
| Recurso não encontrado | Use `--verbose` para identificar o caminho e confira se o pacote está completo. Para nomes legados, revise `--zip-encoding`. |

## 🧪 Validado com um jogo real

**[Dweller’s Empty Path](https://tuyoki.itch.io/dwellers-empty-path), de Temmie Chang**, foi usado na validação com RPG Maker **MV 1.6.1**.

| Ambiente | Verificação concluída |
| :--- | :--- |
| **Linux ARM64 em container, com tela virtual · NW.js 0.119.2** | Menu, nova partida, fontes pelo plugin original, save em disco e carregamento após recarregar. |
| **Chromium no macOS · modo navegador** | Menu, nova partida, contexto de áudio ativo e persistência do save após recarregar, com a adaptação do plugin de fontes. |

Na validação local também passaram **22 testes automatizados**, cobrindo detecção de estruturas, ZIP anexado, nomes japoneses, respostas HTTP, limites de caminhos, checksums e preservação dos saves. A detecção de MZ foi verificada com estruturas sintéticas; não houve validação com um jogo MZ completo.

## 📚 Referências

- [RPG Maker MV · formatos de distribuição](https://rpgmakerofficial.com/product/MV_Help/page/01_11_04.html)
- [RPG Maker MZ · formatos de distribuição](https://rpgmakerofficial.com/product/MZ_help-en/01_11_03.html)
- [NW.js · primeiros passos](https://docs.nwjs.io/For%20Users/Getting%20Started/)
- [NW.js · empacotamento e distribuição](https://docs.nwjs.io/For%20Users/Package%20and%20Distribute/)

---

<div align="center">
  <sub>Python padrão · NW.js · RPG Maker MV/MZ</sub>
</div>
