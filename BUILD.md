# Build do Fervura

O build gera dois resultados na pasta `dist\`:

- `dist\Fervura\`: o programa empacotado pelo PyInstaller (Python e bibliotecas embutidos).
- `dist\Fervura-Setup-1.0.0.exe`: o instalador entregue ao usuário final.

O usuário final **não precisa** de Python nem de nenhuma biblioteca. Python e Inno Setup
são necessários **apenas na máquina de build**.

## 1. Preparar a máquina de build (uma vez)

1. **Python 3.12, 64 bits.**
   Se o comando `py` já existe (gerenciador de instalação do Python), basta:
   ```
   py install 3.12
   ```
   Caso contrário, baixe em https://www.python.org/downloads/windows/ e, na instalação,
   mantenha marcado *py launcher*. *Add python.exe to PATH* é opcional.
   Confira em um Prompt de Comando:
   ```
   py -3.12 --version
   ```
   Para usar outra versão (por exemplo, 3.13), defina antes do build: `set PY=py -3.13`.

2. **Inno Setup 6.**
   Baixe em https://jrsoftware.org/isdl.php e instale com as opções padrão.
   O idioma Português (Brasil) já vem incluído.

3. **Internet** durante o build. O script baixa:
   - a `LibreHardwareMonitorLib` 0.9.6 e as dependências dela, do nuget.org;
   - o `PawnIO_setup.exe` 2.2.0 oficial, do GitHub. O SHA-256 é conferido e o build
     falha se o arquivo não for exatamente o esperado.

## 2. Copiar o projeto para o Windows

Copie a pasta `fervura` para um caminho do Windows, por exemplo
`C:\dev\fervura`. Não rode o build pelo caminho `\\wsl.localhost\...`: o
PyInstaller e o Inno Setup não funcionam bem em caminhos de rede.

## 3. Rodar o build

Não precisa ser administrador.

No **PowerShell** (padrão do Terminal do Windows), o `.\` antes do nome é obrigatório:

```powershell
cd C:\dev\fervura
.\build.bat
```

No **Prompt de Comando** (`cmd`):

```
cd C:\dev\fervura
build.bat
```

O script, em ordem:

1. cria o ambiente `.venv-build` e instala as dependências de `requirements-build.txt` com
   `pip install --upgrade` (pacotes já instalados no ambiente também sobem até as versões
   mínimas pedidas, como `Pillow>=13.0.0` e `setuptools>=84.1.0`). O `setuptools` é usado só no
   build: o PyInstaller roda com `--exclude-module setuptools` e ele não vai para o programa;
2. roda os testes (`tests\test_protocol.py`, `tests\test_app.py`, `tests\test_datadir.py`,
   `tests\test_updates.py`);
3. roda `tools\prepare_build.py`, que baixa a biblioteca e o PawnIO e gera o ícone, a lista
   de componentes exibida pelo instalador e os avisos de terceiros;
4. empacota com o PyInstaller em `dist\Fervura\`;
5. lê a versão de `fervura\__init__.py`, mostra `Versao: X.Y.Z` e compila o instalador com o
   Inno Setup em `dist\` (`/DAppVersion=X.Y.Z`).

Se algo falhar, o script para e mostra `=== FALHOU`. A mensagem logo acima indica a etapa.

Para mudar a versão, edite só `__version__ = "X.Y.Z"` em `fervura\__init__.py`. O build a passa
ao Inno Setup e para com "Versao invalida ou ausente…" se ela não estiver no formato `X.Y.Z`. O
`installer\Fervura.iss` não guarda a versão: compilado à mão sem `/DAppVersion`, gera
`Fervura-Setup-0.0.0-dev.exe`.

## 4. Testar antes de distribuir

Use de preferência uma máquina ou VM limpa, **sem Python** e **sem o PawnIO**.
Para remover um PawnIO já instalado: *Configurações → Aplicativos → PawnIO → Desinstalar*.
Feche também qualquer outro programa que use o display: só um programa por vez abre a porta COM.

### 4.1 Instalação

1. Execute `Fervura-Setup-1.0.0.exe` e aceite o UAC.
2. A tela de informações deve listar todos os componentes: Python, cada biblioteca com
   versão e licença, a LibreHardwareMonitorLib e o PawnIO.
3. Ao clicar em *Instalar*, deve aparecer a pergunta sobre o driver PawnIO.
   - **Teste da recusa:** responda *Não*. O instalador deve parar com
     "Não é possível continuar a instalação sem o driver PawnIO."
   - Rode de novo e responda *Sim*. Deve aparecer a página "Driver PawnIO" com o progresso.
     Anote se o instalador do PawnIO abriu uma janela própria ou se instalou silenciosamente.
4. Ao final, abra o Fervura pelo menu Iniciar (o instalador não o abre automaticamente).

### 4.2 Administrador e inicialização com o Windows

O Fervura sempre roda como administrador (necessário para a temperatura da CPU).

1. Abra pelo menu Iniciar: o Windows deve pedir o UAC, e a temperatura da CPU deve aparecer.
2. Marque *Iniciar com o Windows*. Deve surgir a tarefa **Fervura** no Agendador de Tarefas
   (`taskschd.msc`), com *Executar com privilégios mais altos*.
3. Saia da conta do Windows e entre de novo: cerca de 10 segundos depois do logon, o ícone deve
   aparecer na bandeja **sem** pedir o UAC.
4. Desmarque a opção: a tarefa deve sumir do Agendador.

Se algo falhar, envie o arquivo `%ProgramData%\Fervura\log.txt` (normalmente
`C:\ProgramData\Fervura\log.txt`; ler não exige administrador).

### 4.3 Execuções seguintes

1. Feche o programa (*Sair* no menu do ícone) e abra pelo menu Iniciar. Ele deve abrir direto,
   sem nenhuma instalação (apenas o UAC).
2. Desinstale o PawnIO e abra o programa de novo. Ele deve perguntar se quer instalar o driver;
   recusando, deve mostrar "Não é possível continuar sem o driver PawnIO." e fechar.
3. Desinstale o Fervura (*Configurações → Aplicativos*). A tarefa **Fervura** do Agendador
   deve ser removida junto. O PawnIO permanece instalado, porque é um driver compartilhado
   que outros programas também podem usar; ele tem desinstalação própria.

## Avisos conhecidos

- **Antivírus:** executáveis gerados pelo PyInstaller às vezes são marcados por engano.
  Assinar o `.exe` e o instalador com um certificado de assinatura de código reduz isso.
- **Instalador não assinado:** sem assinatura digital, o Windows SmartScreen mostra
  "Editor desconhecido" ao executar o instalador.
