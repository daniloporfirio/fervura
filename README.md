# Fervura

O Fervura é um software para Windows que mostra a temperatura da CPU ou da GPU em um **display USB de
temperatura de dois dígitos**. Ele fica na bandeja do sistema, com a temperatura atual no ícone, e
tem uma janela opcional com informações do computador.

Atende toda a linha Pichau Aqua:
- Aqua 120X
- Aqua 240X
- Aqua 360X

### O que ele faz

- Lê a temperatura da CPU e da GPU (via [LibreHardwareMonitorLib](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
  e o driver [PawnIO](https://pawnio.eu)) e o uso de CPU e RAM (via `psutil`).
- Envia ao display, pela porta serial, um número de 0 a 99: a temperatura da CPU, a da GPU ou
  a maior das duas.
- Encontra o display sozinho pelo VID/PID do chip USB e reconecta se o cabo for removido.
- Pode iniciar com o Windows (tarefa agendada, sem pedir o UAC a cada logon).

### Requisitos

- Windows 10 versão 1809 ou mais novo, ou Windows 11, 64 bits.
- Permissão de administrador: a temperatura da CPU só pode ser lida assim.
- Driver PawnIO 2.x. O instalador do Fervura pergunta antes de instalá-lo e usa o instalador
  oficial, sem modificações.
- Um display compatível com o protocolo descrito na [especificação](specification.md#3-protocolo-do-display-protocolpy).

### Instalação

Baixe o `Fervura-Setup-<versão>.exe` na página de *Releases*, execute e aceite os termos. Depois de instalar,
abra o Fervura pelo menu Iniciar. Pronto, agora é só usar!

Observação: O executável **não é assinado digitalmente**. Por isso o SmartScreen pode mostrar "Editor
desconhecido", e o Controle de Aplicativo Inteligente do Windows 11, quando ligado, pode
bloquear o programa.

### Uso

Fechar a janela só a oculta; o Fervura continua na bandeja. Pelo ícone é possível abrir a
janela, escolher a fonte da temperatura e sair.

Argumentos de linha de comando para curiosos:

| Argumento | Efeito |
|---|---|
| `--port COMx` | Usa essa porta em vez de procurar o display pelo VID/PID |
| `--interval N` | Segundos entre envios (padrão 1, mínimo 0,1) |
| `--dry-run` | Não abre a serial; só registra os pacotes no log |
| `--minimized` | Inicia só na bandeja |
| `-v`, `--verbose` | Log detalhado |

A configuração e o log ficam em `C:\ProgramData\Fervura`, numa pasta protegida em que só
administradores escrevem.


# Gerar novas versões do fervura
Se você quer alterar o fervura por conta própria e gerar um novo executável basta seguir o guia abaixo.

### Agentes de IA
O Fervura foi feito com o auxilio de IA. O projeto conta com alguns [agentes](https://github.com/daniloporfirio/Fervura/tree/main/.claude/agents) para facilitar o desenvolvimento:
- Tadeu: Programador.
- Regis: Primeiro Code Review.
- Luana: Segundo Code Review com foco em segurança.
- Patricia: Documentador, responsável por atualizar a [especificação](https://github.com/daniloporfirio/Fervura/blob/main/specification.md) onde está o esquema arquitetural do software.

Dentro do [CLAUDE.md](https://github.com/daniloporfirio/Fervura/blob/main/CLAUDE.md) é configurado a orquestração dos agentes de forma automática, primeiro o tadeu cria a funcionalidade, depois o regis faz o primeiro code review, depois a luana faz o segundo code review e por fim a patricia atualiza a especificação do projeto. Caso o regis ou luana não aprove o code review, a task volta para o tadeu gerando um loop de iteração entre eles até que os dois reviewers aprovem a tarefa.

### Build
Para gerar um novo instalador .exe você precisa de:
- Python 3.12 (64 bits)
- Inno Setup 6.

No Prompt de Comando:
```
build.bat
```

O script cria um ambiente virtual, roda os testes, baixa as dependências nativas (com o
SHA-256 do PawnIO conferido), empacota com o PyInstaller e gera o instalador em `dist\`.
Detalhes em [BUILD.md](BUILD.md).

Para rodar só os testes automatizados:

```
py -3.12 -m unittest tests/test_protocol.py tests/test_app.py tests/test_datadir.py -v
```

### Documentação

- [specification.md](specification.md): como o programa funciona e as regras que precisam ser
  preservadas.
- [testing_guide.md](testing_guide.md): testes automatizados e manuais obrigatórios antes de
  cada versão.
- [BUILD.md](BUILD.md): como gerar o executável e o instalador.

### Licença

O código do Fervura está sob a [licença MIT](LICENSE).

O instalador inclui componentes de terceiros com licenças próprias, listadas no
`THIRD_PARTY_NOTICES.txt` gerado pelo build: Python, as bibliotecas Python, a
LibreHardwareMonitorLib (MPL-2.0) e o instalador oficial do PawnIO (GPL-2.0 com exceção para
uso via IOCTL), redistribuído sem modificações.
