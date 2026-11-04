# Fervura — especificação do software

Planta completa do Fervura: o que ele faz, como cada parte funciona e quais regras precisam
ser preservadas. Serve para recriar o programa, portá-lo para outra linguagem ou retomar o
trabalho em uma sessão futura.

Documentos relacionados:

- `BUILD.md`: como gerar o executável e o instalador.
- `testing_guide.md`: testes que todas as versões precisam passar.

---

## 1. Propósito

O Fervura controla um **display USB de temperatura de dois dígitos**. Ele:

1. lê a temperatura da CPU e da GPU;
2. envia um número de 0 a 99 ao display pela porta serial;
3. fica na bandeja do sistema, com uma janela opcional que mostra informações do computador.

### Fatos sobre o hardware

Estes fatos são a base do protocolo implementado (seção 3) e foram confirmados em testes no
aparelho real. Não mudam sem novo teste.

| Fato | Origem |
|---|---|
| O display mostra **apenas dois dígitos**. | Teste no aparelho |
| O display mostra **somente o byte 2** do pacote (campo "temperatura da CPU"), como número cru. Os outros campos e a flag de Fahrenheit são ignorados. | Teste no aparelho |
| Porta serial a **1.000.000 baud, 8N1, sem controle de fluxo**. | Especificação do protocolo |
| VID/PID aceitos, nesta ordem: **`1F3A:0008`**, depois **`1A86:484A`**. | Especificação do protocolo |
| Comunicação só de ida: o display não responde. | Especificação do protocolo |
| A temperatura da CPU no Windows **só é lida com privilégios de administrador** e com o driver PawnIO instalado. | Teste no Windows |

---

## 2. Visão geral da arquitetura

```
                    ┌───────────────────────── processo Fervura.exe (administrador) ──────────────────────────┐
                    │                                                                                          │
 PawnIO (driver) ◄──┤ LibreHardwareMonitorLib (.NET Framework, via pythonnet) ─┐                               │
                    │                                                           ├─► SensorHub ─► Sender ─► serial ├──► display
 Windows/psutil ◄───┤ psutil (uso/clock da CPU, uso de RAM) ───────────────────┘      (thread)    │              │
                    │                                                                            ▼              │
                    │                                              fila de Updates ──► App (janela Tk + bandeja)│
                    └──────────────────────────────────────────────────────────────────────────────────────────┘
```

| Camada | Tecnologia atual | Papel |
|---|---|---|
| Linguagem | Python 3.12 (64 bits) | Todo o programa |
| Interface | tkinter (`ttk`) | Janela |
| Bandeja | `pystray` + `Pillow` | Ícone com o número, menu |
| Serial | `pyserial` | Comunicação com o display |
| Sensores | `LibreHardwareMonitorLib` 0.9.6 via `pythonnet` (.NET Framework 4.7.2+) | Temperaturas, dados da GPU |
| Sensores (complemento) | `psutil` | Uso e clock da CPU, uso de RAM |
| Driver | PawnIO 2.2.0 (instalador oficial, de terceiros) | Acesso de baixo nível para a temperatura da CPU |
| Empacotamento | PyInstaller (pasta única, `--windowed`, `--uac-admin`) | `Fervura.exe` sem exigir Python |
| Instalador | Inno Setup 6 | `Fervura-Setup-<versão>.exe` |

### Estrutura do projeto

```
fervura/
├── iniciar.pyw              ponto de entrada (código-fonte e PyInstaller)
├── fervura/
│   ├── __main__.py          permite "python -m fervura"
│   ├── app.py               inicialização, Sender (thread), App (janela), regras de exibição
│   ├── protocol.py          pacote serial de 23 bytes
│   ├── device.py            descoberta da porta e conexão serial
│   ├── sensors.py           leitura de sensores (LHM + psutil)
│   ├── system.py            PawnIO, administrador, instância única, caminhos; pastas do
│   │                        sistema (ProgramData, Windows, Fonts) e usuário do token, sem
│   │                        variáveis de ambiente
│   ├── datadir.py           pasta de dados protegida (%ProgramData%\Fervura) e checagem de links
│   ├── settings.py          config.json, migração e tarefa agendada de inicialização
│   └── tray.py              ícone e menu da bandeja
├── tests/                   testes automatizados (unittest)
├── tools/prepare_build.py   baixa dependências nativas e gera listas do instalador
├── tools/icon_proposal.py   gera a proposta de ícone (não usada no build; seção 7.4)
├── assets/proposta-icone/   arquivos da proposta de ícone
├── installer/Fervura.iss    script do Inno Setup
├── build.bat                build completo no Windows
├── requirements.txt         dependências de execução
└── requirements-build.txt   dependências de build (inclui PyInstaller)
```

---

## 3. Protocolo do display (`protocol.py`)

### 3.1 Pacote

23 bytes; campos de vários bytes em **big-endian**.

| Offset | Bytes | Campo | Valor enviado pelo Fervura |
|---|---|---|---|
| 0 | 1 | Cabeçalho `0x74` (`'t'`) | fixo |
| 1 | 1 | Flag Fahrenheit (0/1) | sempre 0 |
| 2 | 1 | Temperatura da CPU | **o valor a exibir (0–99)** |
| 3–4 | 2 | Ventoinha da CPU | 0 |
| 5 | 1 | Uso da CPU | 0 |
| 6–7 | 2 | Clock da CPU | 0 |
| 8 | 1 | Uso da GPU | 0 |
| 9–10 | 2 | Clock da GPU | 0 |
| 11–14 | 4 | Memória da GPU usada | 0 |
| 15–18 | 4 | Memória total da GPU | 0 |
| 19–20 | 2 | Ventoinha da GPU | 0 |
| 21 | 1 | Temperatura da GPU | 0 |
| 22 | 1 | Uso de RAM | 0 |

**Ofuscação:** bytes 2 a 22 recebem XOR `0x02`. Os bytes 0 e 1 não são alterados.

Os campos zerados existem porque o firmware pode validar cabeçalho e tamanho. O pacote deve
**sempre** ter 23 bytes e começar com `0x74`.

### 3.2 Funções

- `build_frame(readings, show_fahrenheit=False) -> bytes`: monta o pacote. Cada valor é
  arredondado e limitado (*clamp*) ao intervalo do campo: 0–255 (1 byte), 0–65535 (2 bytes),
  0–4294967295 (4 bytes). Valores negativos viram 0.
- `parse_frame(frame) -> (Readings, bool)`: operação inversa, usada em testes e para
  conferir tráfego capturado. Rejeita pacotes com tamanho ≠ 23 ou cabeçalho ≠ `0x74`.

### 3.3 Exemplo de referência

Valor exibido 54, demais campos zerados:

```
74 00 34 02 02 02 02 02 02 02 02 02 02 02 02 02 02 02 02 02 02 02 02
```

Pacote completo (todos os campos preenchidos), usado no teste de layout:

```
Valores: cpu 55 °C, ventoinha 1200, uso 30, clock 4500, GPU uso 20, clock 1800,
         mem 4096/8192, ventoinha 1000, temp 60, RAM 45, Fahrenheit desligado
Enviado: 74 00 35 06 B2 1C 13 96 16 05 0A 02 02 12 02 02 02 22 02 01 EA 3E 2F
```

---

## 4. Conexão com o display (`device.py`)

### 4.1 Identificação do display (VID/PID) e porta COM

O display tem um chip USB-serial e aparece no Windows como uma **porta serial virtual**
("Dispositivo serial USB (COMx)" em *Portas (COM e LPT)* no Gerenciador de Dispositivos).
Não existe porta serial física.

**O Fervura reconhece o display pelo modelo, não pela unidade.**

| Par VID:PID | Fabricante do VID | Observação |
|---|---|---|
| `1F3A:0008` | Allwinner Technology | Tentado primeiro |
| `1A86:484A` | Nanjing QinHeng Electronics (WCH) | Tentado em segundo |

- **VID** (*Vendor ID*) e **PID** (*Product ID*) são gravados no chip USB pelo fabricante. São
  fixos e **iguais em todas as unidades do mesmo modelo**: qualquer display desse modelo, em
  qualquer computador, é reconhecido.
- Os dois pares e a ordem fazem parte da especificação do protocolo. A explicação mais provável
  para haver dois é o uso de chips diferentes em lotes ou modelos distintos; não confirmado.
  O par usado por um aparelho aparece em *Propriedades → Detalhes → IDs de hardware*
  (ex.: `USB\VID_1A86&PID_484A`).
- **Número de série USB:** alguns aparelhos têm um número de série por unidade; chips USB-serial
  baratos frequentemente não têm, ou repetem o mesmo valor. O Fervura **não usa** número de
  série.
- **Compatibilidade:** só funciona com displays que tenham um desses VID/PID **e** falem o
  protocolo da seção 3. Um display compatível com outro VID/PID funciona com `--port COMx`.
  Um display com outro protocolo não funciona, mesmo que seja encontrado.
- **Falso positivo possível:** `1A86` é o VID da WCH, fabricante de chips USB-serial usados em
  muitos aparelhos. O PID `484A` é pouco comum, mas outro aparelho com o mesmo par seria tratado
  como display e receberia os pacotes. Seriam bytes sem efeito para ele; a solução é `--port`.
  Nenhum caso real conhecido.
- **Vários displays:** é usado o primeiro encontrado, na ordem da tabela.

**Número da porta COM:**

- Quem atribui é o **Windows**, não o display. Na primeira conexão ele usa o menor número
  livre e guarda a associação no registro, por aparelho e por entrada USB.
- O número é estável enquanto o display ficar na mesma entrada USB do mesmo computador. Pode
  mudar em outra entrada USB (principalmente em aparelhos sem número de série), em outro
  computador ou por troca manual (*Gerenciador de Dispositivos → porta → Configurações de
  Porta → Avançado → Número da porta COM*).
- O Fervura **não depende do número**: procura pelo VID/PID a cada conexão. O número só aparece
  no status (`conectado em COMx`) e importa apenas com `--port`.
- Uma porta COM só pode ser aberta por **um programa por vez**. Com outro programa usando o
  display, o Fervura mostra "display não encontrado, tentando de novo…" e conecta sozinho
  quando a porta for liberada.

### 4.2 Funcionamento da conexão

- **Descoberta (`find_port`):** lista as portas COM e devolve a primeira cujo VID/PID seja
  `1F3A:0008`; se não houver, a primeira com `1A86:484A`; senão, nenhuma.
- **Porta fixa:** o argumento `--port COMx` ignora a descoberta.
- **`Display.connect()`:** se ainda não há porta aberta, procura e abre com 1.000.000 baud,
  8 bits, sem paridade, 1 stop bit, sem XON/XOFF nem RTS/CTS e `write_timeout` de 1 s. Sem
  porta, ou com falha ao abrir (porta ocupada, sem permissão, inexistente), devolve `False`.
- **Log das falhas de conexão, uma vez por estado:** as mensagens
  `display not found (VID:PID 1F3A:0008, 1A86:484A)` (nenhuma porta com os VID/PID da tabela 4.1)
  e `could not open <porta>: <erro>` são registradas (WARNING) só quando mudam. A mesma falha
  repetida a cada ciclo não gera nova linha; uma conexão bem-sucedida (`connected on <porta>`)
  zera o estado, e a próxima falha volta a ser registrada.
- **`Display.send(frame)`:** conecta se preciso e escreve o pacote. Se a escrita falhar
  (cabo removido, timeout), registra `write failed, reconnecting: <erro>`, fecha a porta e
  devolve `False`. **A reconexão é automática:** a
  próxima chamada procura e abre a porta de novo.
- Não há leitura de dados do display.

### 4.3 Verificar se um display é compatível

Compatível significa ser **reconhecido** (VID/PID da tabela 4.1) **e** entender o protocolo
(seção 3). O nome comercial do modelo não aparece no Windows (só na etiqueta, na caixa ou na
loja) e não é necessário: o que vale é o VID/PID e o protocolo.

**1. Testar com o Fervura (mais fácil).** Conectar o display e abrir o Fervura.
Status `conectado em COMx` e o número aparecendo no display = compatível nos dois sentidos.
`display não encontrado…` = não reconhecido, ou porta ocupada por outro programa.

**2. Conferir o VID/PID por comando (PowerShell).**

```powershell
Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -match 'VID_1A86&PID_484A|VID_1F3A&PID_0008' } | Select-Object Name, PNPDeviceID
```

Uma linha no resultado (ex.: `Dispositivo serial USB (COM3)` / `USB\VID_1A86&PID_484A\...`) =
reconhecido. Nenhuma linha = outra identificação. Para listar todas as portas seriais e achar a
do display (a que some e volta ao desconectar e reconectar):

```powershell
Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match '\(COM\d+\)' } | Select-Object Name, PNPDeviceID
```

**3. Pelo Gerenciador de Dispositivos.** *Portas (COM e LPT)* → porta do display →
*Propriedades* → *Detalhes*:

- *IDs de hardware*: VID e PID;
- *Descrição do dispositivo relatada pelo barramento*: às vezes traz o nome do produto gravado
  no chip.

**4. VID/PID diferente: testar o protocolo forçando a porta.**

```
"C:\Program Files\Fervura\Fervura.exe" --port COMx
```

Se o número aparecer no display, ele usa o mesmo protocolo. Para passar a reconhecê-lo
automaticamente, acrescente o par em `DEVICE_IDS` (`fervura/protocol.py`) e na tabela 4.1, e gere
uma nova versão. Se o número não aparecer, o display usa outro protocolo e não é suportado.

---

## 5. Leitura de sensores (`sensors.py`)

### 5.1 Dados coletados (`Snapshot`)

Todos os campos são opcionais (`None` quando indisponíveis).

| Campo | Unidade | Fonte |
|---|---|---|
| `cpu_name` | texto | LHM (nome do hardware CPU) |
| `cpu_temp` | °C | LHM, exige PawnIO e administrador |
| `cpu_usage` | % | psutil (`cpu_percent`) |
| `cpu_clock` | MHz | psutil (`cpu_freq().current`) |
| `ram_usage` | % | psutil (`virtual_memory().percent`) |
| `gpu_name` | texto | LHM |
| `gpu_temp` | °C | LHM |
| `gpu_usage` | % | LHM |
| `gpu_clock` | MHz | LHM |
| `gpu_mem_used` / `gpu_mem_total` | MB | LHM |

Ventoinhas **não** são lidas, por decisão de projeto: o display não as mostra.

### 5.2 Inicialização da LibreHardwareMonitorLib

1. Confere se `LibreHardwareMonitorLib.dll` existe na pasta da biblioteca; se não, erro.
2. Adiciona essa pasta ao `sys.path`. O pythonnet procura ali as DLLs dependentes.
3. Carrega o runtime **.NET Framework** (`pythonnet.load("netfx")`), que já vem no
   Windows 10 1809+ e 11. Não exige instalar o .NET 8.
4. `clr.AddReference("LibreHardwareMonitorLib")`.
5. Cria `Computer` com **apenas** `IsCpuEnabled = True` e `IsGpuEnabled = True`. Placa-mãe,
   memória, discos, rede etc. ficam desligados: são desnecessários e reduzem o uso de drivers.
6. `Computer.Open()`.

Pasta da biblioteca: `<_MEIPASS>\lhm` no executável empacotado; `build\vendor\lhm` no código-fonte.

### 5.3 Leitura

A cada ciclo, para cada hardware: `Update()`, depois os sensores dele e dos sub-hardwares.

- **CPU:** o primeiro hardware do tipo `Cpu`.
- **GPU:** preferência `GpuNvidia` > `GpuAmd` > `GpuIntel`, ou seja, placa dedicada antes da
  integrada.

Regra de escolha de sensor (`_pick`), por tipo:

1. Descarta sensores sem valor.
2. Para **temperatura**, descarta valores **≤ 0**. Sem acesso, a biblioteca pode devolver 0
   em vez de "sem valor"; isso não pode virar "0" no display.
3. Percorre os nomes preferidos, nesta ordem, e devolve o primeiro sensor cujo nome os contenha
   (sem diferenciar maiúsculas):

   | Dado | Tipo LHM | Nomes preferidos |
   |---|---|---|
   | Temperatura da CPU | `Temperature` | `package`, `tctl`, `tdie`, `core max`, `core average` |
   | Temperatura da GPU | `Temperature` | `gpu core`, `edge` |
   | Uso da GPU | `Load` | `gpu core`, `d3d 3d` |
   | Clock da GPU | `Clock` | `gpu core` |
   | Memória usada | `SmallData` | `gpu memory used`, `d3d dedicated memory used` |
   | Memória total | `SmallData` | `gpu memory total` |

4. Sem correspondência, devolve o primeiro sensor do tipo com valor > 0; senão, `None`.

Na primeira leitura, registra no log todas as temperaturas encontradas por hardware
(diagnóstico).

`SensorHub.close()` chama `Computer.Close()`.

---

## 6. Regras de exibição (`app.py`)

### 6.1 Fonte do número

Três opções (`settings.SOURCES`):

| Chave | Texto | Regra |
|---|---|---|
| `cpu` | Temperatura da CPU | CPU; se indisponível, usa a GPU e avisa |
| `gpu` | Temperatura da GPU | GPU; se indisponível, usa a CPU e avisa |
| `max` | Maior das duas | Maior entre as disponíveis |

Quando a fonte escolhida falta e a outra é usada, o status mostra
"indisponível; mostrando a temperatura da GPU" (ou CPU). Sem nenhuma temperatura: nada é
enviado e o status diz "sem temperatura para enviar".

### 6.2 Valor enviado

`exibido = limitar(arredondar(temperatura), 0, 99)`, porque o display tem dois dígitos.
O valor vai no byte 2 do pacote; o resto fica zerado.

### 6.3 Ciclo de envio (`Sender`, thread em segundo plano)

A cada `interval` segundos (padrão 1, argumento `--interval`):

1. lê os sensores;
2. calcula o valor (6.1 e 6.2);
3. envia ao display, ou só registra o pacote no log em `--dry-run`;
4. publica um `Update` (dados, valor exibido, observação, status, conectado?) numa fila para a interface.

Exceções dentro do ciclo são registradas e **não** derrubam a thread:

- o traceback completo (`send loop error`, nível ERROR) só é registrado na **primeira** exceção
  de uma sequência igual (mesmo tipo e mesma mensagem). As repetições vão para o log em DEBUG
  (`send loop error repeated: …`). Uma exceção diferente ganha traceback próprio;
- quando um ciclo volta a funcionar, registra uma vez `send loop recovered`.

**Falha ao ler os sensores** (exceção em `SensorHub.read()`): antes de registrar o erro, o ciclo
publica um `Update` com dados vazios, valor `None`, desconectado e o status
"erro ao ler sensores; veja o log.txt". Nada é enviado ao display nesse ciclo; a janela mostra
`--` e `—` nos campos, e o ícone da bandeja fica cinza. **Limitação:** uma exceção em outra parte
do ciclo (fora de `SensorHub.read()`) é registrada, mas não gera `Update`; a interface continua
mostrando o último estado.

| Situação | Status mostrado |
|---|---|
| Enviado com sucesso | `conectado em COMx` |
| Display ausente ou erro de escrita | `display não encontrado, tentando de novo…` |
| Sem temperatura | `sem temperatura para enviar` |
| Erro ao ler os sensores | `erro ao ler sensores; veja o log.txt` |
| `--dry-run` | `simulação (nada é enviado)` |

A linha de status junta, separados por " — ", o status do ciclo, a observação de troca de fonte
(6.1) e, enquanto durar, o erro de gravação da configuração:
"configuração não salva: <erro>" (seção 7.3). Esse último trecho permanece em todas as
atualizações até um salvamento dar certo.

---

## 7. Interface

### 7.1 Janela

Título "Fervura", tamanho fixo. Fechar no **X oculta** a janela; o programa continua na bandeja.

1. **Display**
   - número grande: o valor sendo enviado, ou `--`;
   - três opções de fonte (6.1);
   - linha de status (6.3), com a observação de troca de fonte quando houver.
2. **Sistema** (só visualização)
   - CPU: nome, temperatura, uso, clock;
   - GPU: nome, temperatura, uso, clock, memória `usada / total MB`;
   - uso de RAM;
   - valores ausentes aparecem como `—`;
   - avisos em laranja, abaixo dos dados (podem aparecer os dois, em linhas separadas):
     - temperatura da CPU indisponível:
       "Temperatura da CPU indisponível. Veja o log.txt em %ProgramData%\Fervura."
     - pasta de dados recusada (modo seguro, seção 7.3):
       "A pasta %ProgramData%\Fervura não pôde ser protegida: a configuração não é salva e não há log.txt."
3. **Opções**
   - caixa **Iniciar com o Windows** (seção 9);
   - botões **Ocultar** e **Sair**.

A interface consulta as filas a cada 200 ms e mostra só o `Update` mais recente.
Em caso de erro ao mudar a inicialização automática, a mensagem aparece no status e a caixa
volta ao estado real.

### 7.2 Bandeja do sistema

- **Ícone:** quadrado arredondado 64×64 com o número exibido (ou `--`) em branco.
  - **vermelho** `(200, 30, 40)` quando o último envio foi bem-sucedido;
  - **cinza** `(90, 90, 90)` caso contrário.
  - O ícone só é redesenhado quando o número ou o estado de conexão mudam.
  - **Fonte do número:** carregada sempre por **caminho absoluto** na pasta de fontes do Windows
    (`<GetSystemWindowsDirectoryW>\Fonts`, seção 7.3.4): `segoeuib.ttf`, depois `arialbd.ttf`.
    Se nenhuma abrir, ou se a pasta do Windows não for obtida, usa a fonte padrão do Pillow.
    Nunca por nome solto: com nome solto o Pillow procura primeiro na pasta atual.
- **Dica (tooltip):** `Fervura: <valor>` + status, limitada a 127 caracteres.
- **Clique esquerdo:** abre a janela (item padrão "Abrir").
- **Menu (clique direito):**
  - Abrir;
  - as três fontes, como opções exclusivas, com a atual marcada;
  - Sair.
- Ações do menu rodam na thread da bandeja e são repassadas à interface por uma fila de eventos;
  o tkinter não pode ser chamado de outra thread.

### 7.3 Persistência e log

Todos os arquivos do Fervura ficam numa **pasta de dados local, por máquina e protegida**:
`%ProgramData%\Fervura` (normalmente `C:\ProgramData\Fervura`), tanto no executável quanto no
código-fonte (`fervura/datadir.py`).

| Arquivo | Conteúdo |
|---|---|
| `config.json` | Fonte escolhida (7.3.1) |
| `log.txt`, `log.txt.1` a `log.txt.3` | Log atual e cópias rotacionadas (7.3.3) |
| `autostart-task.xml` | Temporário: existe só durante a ativação da tarefa agendada (seção 9) |

Motivo: o programa sempre roda como administrador. Numa pasta gravável pelo usuário comum
(como `%APPDATA%`), um processo sem privilégios poderia trocar esses arquivos por links e
redirecionar as gravações, renomeações e exclusões feitas pelo processo elevado.

#### 7.3.1 `config.json`

```json
{
  "source": "cpu"
}
```

- **Por máquina:** todos os usuários do computador usam a mesma fonte.
- Salvo a cada troca de fonte, pela janela ou pelo menu da bandeja, em UTF-8 sem BOM.
- **Só a chave `source` é lida e gravada.** Chaves desconhecidas são descartadas e não voltam
  ao arquivo no próximo salvamento.
- Volta ao padrão `cpu` quando: o arquivo não existe; tem mais de 64 KiB (`MAX_CONFIG_BYTES`);
  o JSON é inválido ou aninhado demais; o JSON é válido mas não é um objeto (`[]`, `null`,
  `"cpu"`, `123`); `source` não é texto ou não é `cpu`, `gpu` ou `max`; o arquivo é link ou
  hard link (7.3.5). Arquivo com BOM UTF-8 (ex.: salvo pelo Bloco de Notas) é aceito.
- Arquivo que seja link ou hard link também não é gravado: o log registra
  `settings not saved: <arquivo> is a link or hard link`, sem mensagem na janela.
- **Erro ao gravar** (ex.: pasta apagada com o programa aberto): a fonte nova vale na sessão,
  o log registra `settings not saved: <erro>` e a linha de status mostra
  "configuração não salva: <erro>" até um salvamento dar certo (6.3).
- Usuários comuns só leem a pasta: editar o arquivo à mão exige administrador.

#### 7.3.2 Migração de versões anteriores

Versões anteriores gravavam a configuração por usuário, em `%APPDATA%\Fervura\config.json`.
Na partida, se a pasta de dados é segura e `config.json` ainda não existe nela:

- lê o arquivo antigo **do usuário que executa** (caminho montado com `%APPDATA%` do ambiente
  do processo; sem a variável, `<pasta pessoal>\Fervura\config.json`), com as mesmas regras de
  7.3.1 (tamanho, JSON, link/hard link);
- se houver `source` válido, grava só essa chave no arquivo novo e registra
  `settings migrated from <arquivo>`; arquivo antigo inválido ou ausente não grava nada;
- falha ao gravar registra `settings not migrated: <erro>` e o programa inicia normalmente;
- **nada em `%APPDATA%` é apagado ou renomeado** (uma exclusão elevada numa pasta controlada pelo
  usuário poderia ser redirecionada); os arquivos antigos continuam lá.

#### 7.3.3 Log

- `log.txt`, UTF-8, nível INFO (DEBUG com `-v`), formato `<data hora> <NÍVEL> <módulo>: <mensagem>`.
- **Rotação** (`RotatingFileHandler`): ao passar de 1.000.000 bytes (`LOG_MAX_BYTES`), `log.txt`
  vira `log.txt.1`, o `.1` vira `.2` e o `.2` vira `.3`; no máximo 3 cópias (`LOG_BACKUPS`), a
  mais antiga é descartada. Nunca existe `log.txt.4`.
- Também vai para o console quando existe um (código-fonte com `python`); o executável e o
  `pythonw` não têm console.
- **Sem log em arquivo** quando:
  - a pasta de dados foi recusada (modo seguro, 7.3.6);
  - `log.txt` ou qualquer `log.txt.1` a `.3` é link ou hard link (checado na partida; a rotação
    renomeia e apaga esses arquivos). Registra no console `<arquivo> is a link or hard link; no log file`;
  - o arquivo não abre (`could not open <arquivo>: <erro>`).

  Nos dois últimos casos a configuração continua sendo salva e a janela não mostra o aviso de
  pasta não protegida.

#### 7.3.4 Resolução do caminho (sem variáveis de ambiente)

O caminho da pasta é calculado uma vez, ao carregar o módulo, **sem variáveis de ambiente**
(`system.program_data_dir`):

1. lê o valor `ProgramData` de
   `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList` (visão de 64 bits do registro,
   valor bruto, sem expandir; tipo `REG_SZ` ou `REG_EXPAND_SZ`). Normalmente
   `%SystemDrive%\ProgramData`; só administradores alteram essa chave;
2. substitui **apenas** `%SystemDrive%` (maiúsculas ou minúsculas) pela unidade da pasta do
   Windows obtida por `GetSystemWindowsDirectoryW` (vem do kernel, não de `%WINDIR%`);
3. o resultado precisa ser local e absoluto (`X:\...`), sem `%` restante e sem partes `..`.
   Outra variável, caminho UNC (`\\servidor\...`), `\\?\`, caminho relativo ou com `..` = não
   resolvido;
4. **sem alternativa pelo ambiente:** não usa `%ProgramData%`, `%SystemDrive%`, `%WINDIR%`,
   `%ALLUSERSPROFILE%` nem `C:\ProgramData` fixo. Não resolvido = modo seguro (7.3.6).

A pasta de fontes da bandeja (`<pasta do Windows>\Fonts`, 7.2) vem do mesmo
`GetSystemWindowsDirectoryW`.

Por quê: o processo elevado (pelo UAC ou pela tarefa agendada) herda o ambiente do usuário não
elevado, que pode mudar essas variáveis em `HKCU\Environment`. O `SHGetKnownFolderPath` também
expande o `%SystemDrive%` do processo, por isso não é usado.

#### 7.3.5 Preparação da pasta (`datadir.prepare`)

Executada no início do programa, antes de configurar o log (seção 8). Devolve "pasta segura" ou
o motivo da recusa.

1. Exige Windows, caminho resolvido (7.3.4) e local absoluto.
2. **Pastas acima:** cada uma, da raiz até a pasta-mãe, é aberta sem seguir link. Recusa se
   alguma for link (*reparse point*: junção ou link simbólico) ou não for pasta, e se a
   pasta-mãe imediata (`C:\ProgramData`) tiver dono diferente de SISTEMA, TrustedInstaller ou
   Administradores. Checado antes de criar qualquer coisa.
3. **Pasta ausente:** criada com `CreateDirectoryW` já com o descritor final, sem nenhum
   instante com permissões herdadas:

   | Item | Valor |
   |---|---|
   | Dono | Administradores |
   | Herança | removida (DACL protegida) |
   | SISTEMA | controle total, `(OI)(CI)` |
   | Administradores | controle total, `(OI)(CI)` |
   | Usuários | leitura e execução, `(OI)(CI)` |
   | SDDL | `O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)` |

4. **Pasta existente:** aberta sem seguir link (`FILE_FLAG_OPEN_REPARSE_POINT`) e recusada se
   for link, não for pasta ou tiver dono diferente de Administradores ou SISTEMA (ex.: criada
   antes por um usuário comum). Aceita, recebe de novo a DACL da tabela por
   `SetKernelObjectSecurity`, **sem propagação** (o `SetSecurityInfo` propagaria as permissões
   herdadas e alteraria também um arquivo de fora ligado à pasta por hard link). O dono
   existente (Administradores ou SISTEMA) é mantido.
5. **Arquivos já existentes** diretamente na pasta: cada arquivo comum recebe dono
   Administradores e fica só com as permissões herdadas da pasta (ACEs explícitas, como
   "Todos: controle total", são removidas). Links, arquivos com hard link e subpastas ficam
   intocados. Falha em qualquer arquivo = recusa.
6. **A recusa nunca apaga, move nem recria nada.**

Sem administrador, criar ou reparar a pasta falha e conta como recusa. Por isso, no
código-fonte sem administrador, o primeiro processo (o que se reabre elevado) não grava nada
em disco.

**Arquivos dentro da pasta:** um arquivo que seja link (*reparse point*) ou tenha outro hard
link (mais de um nome) não é lido, gravado nem rotacionado. O log é checado na partida;
`config.json` e `autostart-task.xml`, antes de cada uso.

#### 7.3.6 Modo seguro

Quando a pasta é recusada ou o caminho não é resolvido, nada é gravado em disco:

- o log registra `data folder not used (<motivo>): no log file, settings not saved`, apenas no
  console (no executável, em lugar nenhum);
- `config.json` não é lido nem gravado e não há migração: a fonte começa em `cpu`; uma troca vale
  só na sessão (no log, `settings not saved: data folder unavailable`), sem mensagem na janela;
- "Iniciar com o Windows" não ativa: status
  "não foi possível alterar a inicialização: pasta de dados protegida indisponível (<caminho>); veja o log"
  e a caixa volta ao estado real. Desativar continua funcionando;
- a janela mostra o aviso laranja da pasta não protegida (7.1).

Para sair do modo seguro, um administrador remove o que estava no lugar da pasta (pasta de
outro dono, junção ou arquivo) e abre o Fervura de novo.

### 7.4 Proposta de ícone (não aplicada)

Arte gerada por `tools/icon_proposal.py` em `assets/proposta-icone/`, **sem uso no build**.
O ícone em uso continua o da seção 7.2 (e o mesmo desenho no `.exe` e no instalador).

- **Conceito "fervura":** chama em degradê âmbar `#ffc43d` → vermelho `#e22b2b`, com núcleo claro
  `#ffecaa` e brilho suave, mais três bolhas subindo (`#ffd68c`), sobre quadrado arredondado
  escuro `#1c1c1c` com borda sutil `#464646`, que destaca o ícone em barras de tarefas escuras.
- **Tamanhos:** de 16 a 32 px, versão simplificada (chama maior, sem bolhas); a partir de 48 px,
  arte completa. O `.ico` tem 16, 24, 32, 48, 64, 128 e 256 px.
- **Bandeja:** mantém o número ao vivo (legibilidade em 16 px); fundo em degradê âmbar →
  vermelho quando conectado e cinza `#787878` → `#464646` quando não.
- **Arquivos:** `fervura.ico`, `fervura-256.png`, `bandeja-conectado.png`,
  `bandeja-desconectado.png` e `previa.png` (folha de prévia em fundo claro e escuro).
- **Gerar de novo:** `python tools/icon_proposal.py` (precisa do Pillow).

**Para adotar:**

1. ícone do `.exe` e do instalador: em `prepare_build.make_icon`, copiar
   `assets/proposta-icone/fervura.ico` para `build/icon.ico` em vez de desenhar;
2. ícone da bandeja: trocar o fundo sólido de `tray.render` pelo degradê de
   `icon_proposal.tray_icon`;
3. atualizar esta seção, a 7.2 e os casos `BLD-09` e `TRY-01` do guia de testes.

---

## 8. Inicialização do programa (`main`)

Ordem obrigatória:

1. Lê os argumentos: `--port`, `--interval`, `--dry-run`, `--minimized`, `-v`. Argumento
   inválido (ex.: `--interval 0`) encerra aqui com o erro do `argparse` (código 2), antes de
   log, Tk ou elevação. No executável, que não tem console, a saída é silenciosa.
2. **Pasta de dados:** prepara `%ProgramData%\Fervura` (7.3.5). Recusada = modo seguro (7.3.6).
3. Configura o log (arquivo só se a pasta for segura, 7.3.3) e registra
   `iniciando; administrador=<True/False>`; depois, se for o caso, o motivo de não usar a pasta
   ou o arquivo de log.
4. Cria a raiz Tk **oculta**: os diálogos aparecem antes da janela.
5. **Administrador:**
   - o executável empacotado já pede o UAC pelo manifesto (`--uac-admin`);
   - rodando pelo código-fonte sem administrador, o programa se reabre elevado (`runas`) e
     encerra. Esse primeiro processo não consegue preparar a pasta (passo 2), então não grava
     nada em disco; quem prepara a pasta e grava o log é o processo elevado;
   - se o UAC for recusado: erro "O Fervura precisa de permissão de administrador para ler a temperatura da CPU." e sai.
6. **Instância única:** mutex `FervuraMutex`. Se já existir, mostra "O Fervura já está em
   execução. Use o ícone na bandeja do sistema." (exceto com `--minimized`) e sai.
   O mesmo nome é usado pelo instalador (`AppMutex`) para detectar o programa aberto.
7. **PawnIO** (seção 10.1). Ausente ou com versão < 2.0:
   - pergunta se deseja instalar;
   - **Não:** "Não é possível continuar sem o driver PawnIO." e sai;
   - **Sim:** executa `third_party\PawnIO\PawnIO_setup.exe -install` e aguarda;
   - instalador não encontrado: "Instalador do PawnIO não encontrado… Reinstale o Fervura." e sai;
   - driver continua ausente depois: "O driver PawnIO não foi instalado. Não é possível continuar." e sai.
8. **Sensores:** inicializa o `SensorHub`. Falha: "Não foi possível carregar a leitura de sensores: <erro>" e sai.
9. Migra a configuração antiga, se for o caso (7.3.2); carrega `config.json`; cria o `Sender` e a janela.
10. Mostra a janela, a menos que `--minimized`.
11. Inicia a bandeja (thread própria), o `Sender` e o laço da interface.

**Sair** (janela ou bandeja): sinaliza a parada do `Sender`, remove o ícone da bandeja e fecha a
janela. Ao terminar o laço da interface, o programa espera o `Sender` terminar por **até 3 s**
(`SHUTDOWN_TIMEOUT`). Ao terminar, o `Sender` fecha a porta serial e chama `SensorHub.close()`
(que chama `Computer.Close()`), em `try/finally`: o segundo roda mesmo se o primeiro falhar. Se
o `Sender` estiver travado, registra `sender did not stop within 3 s; exiting anyway` e sai
assim mesmo: a thread é *daemon* e não impede o fim do processo.

### Argumentos de linha de comando

| Argumento | Efeito |
|---|---|
| `--port COMx` | Usa essa porta em vez de procurar por VID/PID |
| `--interval N` | Segundos entre envios (padrão 1). Aceita só número finito ≥ 0,1 (`MIN_INTERVAL`); `0`, negativos, `0.05`, `nan`, `inf` ou texto são recusados pelo `argparse` ("número inválido: …" ou "use um número de segundos maior ou igual a 0.1"), com código de saída 2 |
| `--dry-run` | Não abre a serial; registra os pacotes no log |
| `--minimized` | Inicia só na bandeja (usado pela inicialização automática) |
| `-v`, `--verbose` | Log em nível DEBUG |

---

## 9. Inicialização com o Windows (`settings.py`)

Programas que exigem administrador **não** podem iniciar pela chave `Run` do registro, porque
o Windows bloqueia o UAC no logon. Por isso a opção usa uma **tarefa agendada**:

| Propriedade | Valor |
|---|---|
| Nome | `Fervura` |
| Gatilho | Logon do usuário atual, atraso de 10 s |
| Usuário (`UserId` do gatilho e do principal) | `DOMÍNIO\usuário` lido do token do processo (`GetUserNameExW`, formato `NameSamCompatible`), **não** de `%USERDOMAIN%`/`%USERNAME%`. Sem o usuário, a ativação falha com "não foi possível obter o usuário atual: …" |
| Privilégio | `HighestAvailable` (executar com privilégios mais altos), `InteractiveToken` |
| Ação | `"<Fervura.exe>" --minimized` (no código-fonte: `pythonw.exe iniciar.pyw --minimized`) |
| Instâncias | `IgnoreNew` |
| Bateria | inicia e continua mesmo na bateria |
| Tempo limite | nenhum (`PT0S`) |

- **Ativar:**
  1. exige a pasta de dados segura (7.3). Em modo seguro, falha com
     "pasta de dados protegida indisponível (<caminho>); veja o log";
  2. o XML vai para `%ProgramData%\Fervura\autostart-task.xml`, onde nenhum outro processo pode
     trocá-lo antes de o `schtasks` ler. Se esse arquivo for link ou hard link, falha com
     "arquivo inseguro (link): <arquivo>". Uma sobra comum de execução interrompida é apagada;
  3. grava o XML em UTF-16, criando o arquivo em modo exclusivo (falha se ele reaparecer);
  4. executa `schtasks /Create /TN Fervura /XML <arquivo> /F` e apaga o XML, mesmo se o comando falhar.

  As falhas aparecem no status como "não foi possível alterar a inicialização: <erro>" (7.1).
- **Desativar:** `schtasks /Delete /TN Fervura /F`.
- **Estado da caixa:** `schtasks /Query /TN Fervura` (código 0 = existe).
- Os comandos rodam sem abrir janela de console (`CREATE_NO_WINDOW`).
- O desinstalador remove a tarefa.

---

## 10. Dependências de sistema (`system.py`)

### 10.1 Detecção do PawnIO

Mesmo critério da LibreHardwareMonitor: lê `DisplayVersion` em
`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO`, nas visões de 64 e 32 bits
do registro. Instalado = versão ≥ **2.0.0**.

Leitura da versão (`parse_version`), até três partes separadas por ponto:

- cada parte vale pelo seu **prefixo numérico**; depois de uma parte com sufixo, a leitura para
  (`2.0.0-beta` → 2.0.0; `2.3b.7` → 2.3.0);
- partes ausentes valem 0 (`2` → 2.0.0; `2.1` → 2.1.0); partes além da terceira são ignoradas;
- só dígitos ASCII `0`–`9` contam (dígitos de outros alfabetos, como `٢` ou `２`, não); até 9
  dígitos por parte, mais que isso = versão inválida;
- sem número no início (`""`, `abc`, `v2.0`) = versão ausente.

Exemplos: `2`, `2.0`, `2.0.0-beta` e `2.2.0` são aceitos; `1.9.9` é recusado (antigo). A
consulta (`pawnio_version`) nunca lança exceção: valor estranho no registro = PawnIO ausente.

### 10.2 Instalação do PawnIO

- Instalador: `PawnIO_setup.exe` oficial **sem modificações**, de namazso (https://pawnio.eu),
  versão **2.2.0**, SHA-256 `1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032`.
- Argumento: `-install`.
- Executado direto se o processo já é administrador; senão, por `ShellExecuteEx` com `runas`,
  aguardando o término.
- A redistribuição sem modificações é permitida pelos termos do próprio instalador.
- O PawnIO **não** é removido ao desinstalar o Fervura: é um driver compartilhado com outros
  programas e tem desinstalação própria.

### 10.3 Caminhos

| Recurso | Executável empacotado | Código-fonte |
|---|---|---|
| DLLs da LHM | `<_MEIPASS>\lhm` | `build\vendor\lhm` |
| Instalador do PawnIO | `<pasta do exe>\third_party\PawnIO\PawnIO_setup.exe` | `build\vendor\pawnio\PawnIO_setup.exe` |
| Comando para reabrir/autostart | `Fervura.exe` | `pythonw.exe iniciar.pyw` |
| Pasta de dados (`config.json`, `log.txt`, XML temporário) | `%ProgramData%\Fervura` (7.3.4) | `%ProgramData%\Fervura` (a mesma) |
| Configuração antiga (só leitura, migração) | `%APPDATA%\Fervura\config.json` | igual |
| Fontes da bandeja | `<pasta do Windows>\Fonts` | igual |

Pastas do sistema (ProgramData, Windows, Fonts) e o usuário da tarefa agendada são obtidos sem
variáveis de ambiente (7.3.4 e seção 9). A exceção é o caminho da configuração antiga, que usa
`%APPDATA%` (só leitura; seção 15).

---

## 11. Instalador (`installer/Fervura.iss`)

| Item | Valor |
|---|---|
| Ferramenta | Inno Setup 6, idioma Português (Brasil) |
| AppId | `{49026729-6B1B-48E0-BE60-D5E0C6352593}`. **Não mudar entre versões**: identifica o produto para atualizações e desinstalação |
| Privilégios | administrador |
| Arquitetura | somente x64 |
| Windows mínimo | 10 versão 1809 (build 17763), exigência do PawnIO |
| Pasta padrão | `C:\Program Files\Fervura` |
| Saída | `dist\Fervura-Setup-<versão>.exe` |
| Detecção do programa aberto | `AppMutex=FervuraMutex`, `CloseApplications=yes` |
| Log | `SetupLogging=yes` (em `%TEMP%\Setup Log *.txt`) |

Fluxo:

1. **Tela de informações** (`componentes.txt`, gerada no build) listando explicitamente:
   - o programa, e que ele roda como administrador;
   - a versão do Python embutido;
   - cada biblioteca Python com versão e licença;
   - a LibreHardwareMonitorLib e cada dependência .NET com versão e licença;
   - o driver PawnIO, como software de terceiros, com o aviso de que haverá consulta antes.
2. Pasta de instalação e tarefa opcional "Criar ícone na área de trabalho" (desmarcada).
3. **Ao clicar em Instalar (`PrepareToInstall`):**
   - PawnIO ≥ 2.0 presente: registra no log e segue sem perguntar.
   - Ausente: pergunta, citando autor, site, versão e "sem modificações".
     - **Não:** a instalação para com "Não é possível continuar a instalação sem o driver PawnIO."
     - **Sim:** página de progresso "Driver PawnIO" (extraindo → executando
       `PawnIO_setup.exe -install` → verificando). Se o driver não aparecer no registro depois,
       a instalação para com mensagem e código de saída.
4. Cópia dos arquivos:
   - o programa;
   - `third_party\PawnIO\PawnIO_setup.exe`, para reinstalações feitas pelo próprio programa;
   - `THIRD_PARTY_NOTICES.txt`.
5. Atalhos no menu Iniciar (e na área de trabalho, se marcado).
6. **Não** abre o programa ao final. Num teste, o programa iniciado pelo instalador foi
   bloqueado com o erro 4551 (política de controle de aplicativo), e aberto pelo menu Iniciar
   funcionou. A causa provável é o Controle de Aplicativo Inteligente bloqueando o executável
   **sem assinatura**, e não o fato de ser aberto pelo instalador; não confirmado (seção 15).

Desinstalação: remove os arquivos e a tarefa agendada `Fervura`. Mantém
`%ProgramData%\Fervura` (configuração e log), o `%APPDATA%\Fervura` de versões anteriores e o
PawnIO. O script do instalador não cria nem remove a pasta de dados: ela é criada pelo próprio
programa (7.3.5).

---

## 12. Build (`build.bat`, `tools/prepare_build.py`)

Executado no Windows. Detalhes em `BUILD.md`.

1. Confere o Python do build (`py -3.12` por padrão, ou a variável `PY`), com mensagem de ajuda se faltar.
2. Cria `.venv-build`, atualiza o `pip` e instala `requirements-build.txt` com
   `pip install --upgrade` (atualiza também pacotes já presentes no ambiente às versões mínimas
   pedidas):
   - `requirements.txt` (execução): `pyserial>=3.5`, `psutil>=5.9`, `pystray>=0.19`,
     `Pillow>=13.0.0`, `pythonnet>=3.0.5`;
   - `requirements-build.txt` (só build): `pyinstaller>=6.10`, `setuptools>=84.1.0`. O
     `setuptools` serve apenas ao ambiente de build e não entra no executável.
3. Roda os testes automatizados (`tests\test_protocol.py`, `tests\test_app.py`,
   `tests\test_datadir.py`); uma falha interrompe o build.
4. `prepare_build.py`:
   1. **LibreHardwareMonitorLib 0.9.6** do nuget.org, com todas as dependências transitivas.
      Regras:
      - grupo de dependências preferido: .NET Framework 4.7.2 (depois versões menores, depois .NET Standard 2.0);
      - pastas procuradas: `runtimes/win-x64/lib/<fw>/` antes de `lib/<fw>/`;
      - versão de cada dependência: o mínimo pedido; havendo pedidos diferentes, **vence o maior**;
      - pacotes cuja pasta tem só `_._` já são fornecidos pelo .NET Framework e não geram DLL.
      
      Resultado esperado hoje: 13 DLLs em `build\vendor\lhm`.
   2. **PawnIO_setup.exe 2.2.0** do GitHub oficial; o build **falha** se o SHA-256 não conferir.
   3. **`icon.ico`** gerado com o mesmo desenho da bandeja.
   4. **`componentes.txt`** e **`THIRD_PARTY_NOTICES.txt`**, com as versões reais lidas do ambiente de build.
5. PyInstaller:
   - opções: `--windowed --uac-admin --name Fervura`;
   - inclui `build\vendor\lhm` como `lhm`;
   - `--hidden-import pystray._win32`;
   - `--collect-all pythonnet` e `--collect-all clr_loader`;
   - `--exclude-module setuptools`: o `setuptools` do ambiente de build não é empacotado. O
     resultado esperado (conferido no `SEC-05` do guia) é nenhum `setuptools` nem
     `pkg_resources` em `dist\Fervura\_internal`.
6. Inno Setup (`ISCC.exe`, procurado no PATH e nas pastas padrão).

**Atualizar dependências nativas:** mude `LHM_VERSION`, `PAWNIO_VERSION`, `PAWNIO_URL` e
`PAWNIO_SHA256` em `prepare_build.py`, e `PawnIOVersion` no `.iss`. Atualize a LHM e o PawnIO
juntos: os módulos do PawnIO vêm embutidos na LHM.

---

## 13. Segurança e licenças

- Nenhum driver vulnerável é distribuído. A LHM 0.9.6 usa o PawnIO; WinRing0 e InpOut não
  estão presentes. A `RAMSPDToolkit-NDD.dll` tem uma interface opcional para WinRing0, sem o
  driver, e não é usada (memória desligada).
- PawnIO: GPL-2.0 com exceção para uso via IOCTL; o binário oficial é redistribuído sem
  modificações.
- LibreHardwareMonitorLib: MPL-2.0. Licenças e endereços de código-fonte em `THIRD_PARTY_NOTICES.txt`.
- **Dados do processo elevado em pasta protegida** (7.3): `config.json`, `log.txt` e o XML da
  tarefa ficam em `%ProgramData%\Fervura`, com dono Administradores e DACL sem herança
  (usuários comuns só leem). A pasta é recusada, nunca recriada, se for link, se houver link
  acima dela, se tiver dono não confiável ou se a pasta-mãe tiver dono não confiável. Arquivos
  que sejam link ou hard link não são lidos, gravados nem rotacionados. Recusa = modo seguro,
  sem gravar nada em disco e com aviso visível na janela.
- **Sem variáveis de ambiente para pastas e usuário:** ProgramData (registro HKLM + pasta do
  Windows pelo kernel), Fonts e o usuário da tarefa agendada (token do processo) não dependem
  do ambiente que o processo elevado herda do usuário (7.3.4, seção 9). Fonte da bandeja
  carregada por caminho absoluto, nunca da pasta atual.
- **Entrada não confiável limitada:** `config.json` com no máximo 64 KiB, só a chave `source`;
  JSON aninhado demais vira padrão. Versão do PawnIO lida só com dígitos ASCII, até 9 por
  parte, sem exceção.
- **Configuração antiga:** apenas lida em `%APPDATA%` (com as mesmas checagens); nada é apagado
  nem renomeado lá.
- **Dependências:** `Pillow>=13.0.0`; o build usa `pip install --upgrade`. O `setuptools>=84.1.0`
  fica só no ambiente de build e é excluído do PyInstaller. As versões não são fixadas com hash
  (seção 15).
- Executáveis **não assinados**. O SmartScreen mostra "Editor desconhecido", e o Controle de
  Aplicativo Inteligente pode bloquear o programa e a tarefa agendada (provável causa do erro
  4551, seção 15). Opção futura para projeto open source: SignPath Foundation.

---

## 14. Decisões de projeto (e por quê)

| Decisão | Motivo |
|---|---|
| Enviar só o byte 2 com valor real | É o único que o display mostra |
| Limitar a 0–99 | O display tem dois dígitos |
| Temperatura 0 = indisponível | Sem acesso, a LHM devolve 0; enviar 0 seria enganoso |
| LHM como biblioteca, não como programa | Um processo só, sem servidor web nem janela extra |
| .NET Framework em vez de .NET 8 | Já vem no Windows; nada a instalar |
| Sem leitura de ventoinhas | O display não as mostra |
| Sempre administrador | Teste mostrou que a temperatura da CPU só é lida assim |
| Tarefa agendada em vez da chave `Run` | Programas elevados não iniciam pela `Run` |
| PawnIO oficial incluído, sem modificações | Licença permite; compilar e assinar um driver próprio exige certificado EV e assinatura da Microsoft |
| Recusar o PawnIO interrompe a instalação/execução | Sem ele, a função principal não existe |
| PyInstaller + Inno Setup | O usuário final não tem Python |
| Instalador não abre o programa ao final | Um bloqueio 4551 foi observado ao abrir pelo instalador. A causa provável é o executável sem assinatura (seção 15), não confirmada; a decisão fica até haver confirmação ou assinatura |
| Fahrenheit removido | O display ignora a flag; só exibe o número |
| Proposta de ícone fora do build | Avaliar a arte antes de trocar a identidade visual |
| Dados em `%ProgramData%\Fervura` protegida, não em `%APPDATA%` | O processo elevado gravando numa pasta controlada pelo usuário comum permite redirecionar gravações e exclusões por links |
| Recusar a pasta (modo seguro) em vez de apagar e recriar | Apagar, mover ou seguir algo plantado por outro usuário é exatamente o que um ataque por link explora; recusar não toca em nada |
| Configuração por máquina | Consequência da pasta única em `%ProgramData%`, aprovada pelo usuário ao decidir a mudança; a configuração tem uma única opção (a fonte) |
| Caminhos e usuário sem variáveis de ambiente | O processo elevado herda o ambiente do usuário não elevado, que o controla por `HKCU\Environment` |
| Sem alternativa (`%ProgramData%`, `C:\ProgramData`) quando o caminho não resolve | Uma alternativa pelo ambiente reabriria o mesmo problema; o modo seguro é visível e não grava nada |
| Permissões dos arquivos existentes redefinidas ao abrir | Arquivos criados antes da proteção (ou por versão anterior) podiam ter ACEs explícitas que sobrevivem à troca da DACL da pasta |
| DACL da pasta sem propagação (`SetKernelObjectSecurity`) | A propagação alteraria as permissões de um arquivo externo ligado à pasta por hard link |
| Usuário da tarefa agendada lido do token | `%USERDOMAIN%`/`%USERNAME%` vêm do ambiente e podem ser alterados pelo usuário |
| Migração só copia `source`, sem apagar o arquivo antigo | Exclusão elevada em `%APPDATA%` poderia ser redirecionada; o arquivo antigo é inofensivo |
| Log rotativo (1 MB, 3 cópias) e falhas de conexão uma vez por estado | O log não cresce sem limite com o display ausente ou um erro repetido a cada segundo |
| `--interval` mínimo de 0,1 s | Valores menores, `nan` ou `inf` fariam o ciclo girar sem pausa ou travar |
| Sair espera o `Sender` por até 3 s | Fecha a porta serial e a LHM de forma ordenada, sem deixar uma thread travada impedir a saída |

---

## 15. Pontos em aberto

- Se `PawnIO_setup.exe -install` exibe interface própria ou instala em silêncio. Funcionou no
  teste; não está documentado pelo autor.
- **Erro 4551 e outros bloqueios:** a causa provável é o Controle de Aplicativo Inteligente
  bloqueando o executável sem assinatura (eventos CodeIntegrity 3033/3077, política
  `0283ac0f-fff1-49ae-ada1-8a933130cad6`), e não o fato de ser aberto pelo instalador. Pode
  bloquear também a tarefa agendada de "Iniciar com o Windows". Não confirmado; a solução
  definitiva é assinar o código.
- Comportamento do display quando nada é enviado: mantém o último valor? apaga? Não testado.
- Suporte a vários displays ou várias GPUs: hoje usa o primeiro display encontrado e a GPU de
  maior prioridade.
- **Negação de serviço local:** um usuário comum pode criar `C:\ProgramData\Fervura` antes da
  primeira execução (a pasta-mãe permite criar pastas). O Fervura recusa a pasta e fica em modo
  seguro (sem salvar configuração, sem log, sem ativar a inicialização), com o aviso visível na
  janela; um administrador resolve apagando a pasta. Aceito: não há escalada de privilégio.
- **Configuração compartilhada entre usuários:** a fonte escolhida vale para todos os usuários
  do computador (7.3.1).
- **Exceção fora de `SensorHub.read()`** no ciclo de envio é registrada, mas não gera `Update`:
  a janela e a bandeja continuam mostrando o último estado (6.3).
- **Reforços de segurança futuros** (achados informativos da revisão de segurança, sem correção
  nesta versão, classificados como INFORMATIVA):
  - conferir os direitos efetivos da pasta-mãe, e não só o dono;
  - `is_local_absolute` recusar também `/` e o `:` de fluxos alternativos (ADS);
  - no cenário de pasta pré-existente, handles abertos por outro processo antes do `prepare()`
    continuam válidos depois da troca da DACL;
  - chamar o `schtasks` por caminho absoluto (`<System32>\schtasks.exe`), não pelo `PATH`;
  - incluir a ACE `OWNER RIGHTS` na DACL da pasta, limitando o que o dono pode fazer implicitamente;
  - migração: obter a pasta *RoamingAppData* sem `%APPDATA%` do ambiente (hoje só leitura, com as
    checagens de link e tamanho);
  - fixar as dependências com hashes (`pip --require-hashes`);
  - assinatura de código do executável e do instalador.
