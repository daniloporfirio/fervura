# Fervura — guia de testes

Testes que todas as versões do Fervura precisam passar antes de serem distribuídas. Cobrem
todas as funcionalidades descritas em `specification.md`. Cada caso tem um identificador
(ex.: `INS-03`) para ser citado em relatórios e correções.

## 0. Como usar este guia

| Situação | O que rodar |
|---|---|
| Qualquer mudança no código | Seção 1 (automáticos), que o `build.bat` já executa |
| Antes de distribuir uma versão | Seção 1 + **teste rápido** (seção 3) + casos das áreas alteradas |
| Mudança de versão da LHM, do PawnIO, do Python ou do PyInstaller | Seção 1 + **todos** os casos da seção 4 |
| Mudança no protocolo ou em `device.py` | Seção 1 + `SER-*` + `DSP-*` + `CMP-*` |
| Inclusão de um novo VID/PID (display novo) | Seção 1 + `CMP-*` + `SER-01`, `SER-04`, `SER-10` com o display novo |
| Adoção da proposta de ícone (`specification.md` 7.4) | `BLD-09`, `TRY-01` (atualizar os esperados para a arte nova), `UI-01` |
| Mudança no instalador | `INS-*`, `DES-*`, `AUT-05` |
| Mudança em `datadir.py`, em `settings.py` ou na resolução de pastas/usuário em `system.py` | Seção 1 + `CFG-*` + `DAD-*` + `AUT-01`, `AUT-07` + `INI-03`, `INI-11` |
| Mudança de dependências (`requirements*.txt`) ou das opções do PyInstaller no `build.bat` | Seção 1 + `BLD-01` + `SEC-05` + teste rápido |
| Mudança em `updates.py` ou `shellexec.py` (aviso de atualização) | Seção 1 + `UPD-*` + `SEC-06` + `TRY-02`, `TRY-04` + `UI-01` |
| Mudança da versão (`__version__` em `fervura/__init__.py`) ou da forma como o build a passa ao Inno Setup | Seção 1 + `BLD-01`, `BLD-10`, `BLD-11` + `INI-11` + `INS-12` |

Registre os resultados no modelo da seção 6.

---

## 1. Testes automatizados

Rodam sem hardware e sem administrador; nunca tocam na pasta real `C:\ProgramData\Fervura`
(usam pastas temporárias):

```
python -m unittest tests/test_protocol.py tests/test_app.py tests/test_datadir.py tests/test_updates.py -v
```

São 133 testes em 4 arquivos (89 em `test_protocol.py`, `test_app.py` e `test_datadir.py`; 44
em `test_updates.py`). Nenhum acessa a rede nem o Explorer de verdade (usam respostas e COM
simulados). No Windows, 2 são pulados (`test_no_data_dir_outside_windows` e
`test_not_windows_is_refused`, que só valem fora do Windows). Os testes de caminhos do Windows,
junções e ACL (`DataPathsTest` em parte, `PathWithoutEnvironmentTest`, `PrepareTest`,
`SafeFileTest.test_junction_is_unsafe`) e o de certificados
(`TransportTest.test_certificates_verified_with_the_windows_store_only`) só rodam no Windows;
no Linux/WSL são pulados. Alguns
testes de `PrepareTest` se comportam diferente rodando elevado (indicado na tabela).

No Windows, o `build.bat` os executa e **interrompe o build** se algum falhar.

`tests/test_protocol.py`:

| Teste | Garante |
|---|---|
| `ProtocolTest.test_frame_layout` | Layout dos 23 bytes, big-endian, cabeçalho `0x74`, flag no byte 1, XOR `0x02` a partir do byte 2 (pacote de referência da `specification.md` 3.3) |
| `ProtocolTest.test_roundtrip_and_clamping` | `parse_frame` desfaz `build_frame`; valores acima do campo saturam (300 → 255), negativos viram 0, arredondamento (1799,6 → 1800) |

`tests/test_app.py`:

| Teste | Garante |
|---|---|
| `DisplayValueTest.test_sources` | Regras `cpu`, `gpu` e `max` |
| `DisplayValueTest.test_falls_back_to_other_temperature` | Fonte ausente usa a outra e gera observação; sem temperaturas devolve `None` |
| `SenderTest.test_sends_two_digit_value_in_cpu_slot` | Valor limitado a 99, enviado no byte 2, demais campos zerados, status "conectado" |
| `SenderTest.test_nothing_sent_without_temperatures` | Sem temperatura, nenhum pacote é enviado |
| `SenderTest.test_read_error_publishes_disconnected_update` | Falha em `hub.read()` não derruba o ciclo; publica `Update` vazio, valor `None`, desconectado, status "erro ao ler sensores; veja o log.txt"; nada enviado |
| `SenderTest.test_repeated_error_logs_traceback_once` | Mesmo erro repetido: um só traceback; erro diferente ganha o seu; "send loop recovered" ao voltar a funcionar |
| `SenderTest.test_shutdown_closes_display_and_sensors` | `shutdown(3.0)` para a thread, fecha a serial e chama `close()` dos sensores |
| `SenderTest.test_shutdown_does_not_hang_on_stuck_thread` | Thread travada: `shutdown` desiste no prazo, registra aviso e a thread é *daemon* |
| `PickTest.test_zero_temperature_counts_as_unavailable` | Temperatura 0 é ignorada; um sensor válido é escolhido |
| `SettingsLoadTest` (12 testes: `test_deeply_nested_json_falls_back_to_default`, `test_unknown_keys_are_dropped_and_not_saved`, `test_check_updates_is_a_strict_bool`, `test_check_updates_saved_as_strict_bool`, `test_oversized_file_falls_back_to_default`, `test_storage_not_ready_reads_and_writes_nothing`, `test_hard_linked_config_is_not_read_nor_written`, `test_non_object_json_falls_back_to_default`, `test_invalid_source_type_falls_back_to_default`, `test_corrupted_or_missing_file`, `test_utf8_bom_is_accepted`, `test_save_and_load_roundtrip`) | `config.json`: JSON aninhado demais, maior que 64 KiB, corrompido, ausente ou não-objeto (`[]`, `null`, `"cpu"`, `123`, `true`) → padrão (`cpu`, `check_updates` `false`); `source` inválido → `cpu`; só `source` e `check_updates` são lidas e gravadas; `check_updates` só liga com JSON `true` (`1`, `"true"`, `null`, listas, `1.0` → `false`, também ao gravar); `source` não hasheável ao salvar grava o padrão sem exceção; BOM UTF-8 aceito; sem pasta segura nada é lido nem gravado; hard link não é lido nem gravado (alvo intacto) |
| `SettingsMigrationTest` (5 testes: `test_migrates_only_source_and_keeps_old_file`, `test_existing_new_file_wins`, `test_invalid_or_missing_old_file_writes_nothing`, `test_write_error_does_not_stop_the_start`, `test_no_migration_without_storage`) | Migração de `%APPDATA%`: copia só `source` (`check_updates: true` no antigo não é copiado; o novo fica com `false`); arquivo antigo intacto e nada criado ao lado; arquivo novo existente prevalece; antigo inválido não grava nada; erro de gravação só gera aviso; sem pasta segura não migra |
| `AutostartXmlTest` (5 testes: `test_xml_written_in_data_folder_and_removed`, `test_user_comes_from_the_token_not_the_environment`, `test_leftover_plain_xml_is_replaced`, `test_refused_without_protected_folder`, `test_refused_when_xml_is_hard_link`) | XML da tarefa gravado na pasta de dados e apagado depois; `UserId` (gatilho e principal) vem do token, não de `USERNAME`/`USERDOMAIN`; sobra comum substituída; recusa sem pasta segura ou com XML em hard link (sem chamar o `schtasks`, alvo intacto) |
| `SetSourceSaveErrorTest.test_save_error_is_logged_and_shown` | Erro ao salvar: fonte muda na sessão, aviso no log, status "configuração não salva: …" mantido nas atualizações seguintes |
| `SetSourceSaveErrorTest.test_successful_save_clears_the_error` | Salvamento bem-sucedido limpa a mensagem de erro do status |
| `WarningsTextTest.test_cpu_warning_points_to_program_data` | Aviso de CPU cita `%ProgramData%\Fervura`, não `APPDATA`; some com temperatura |
| `WarningsTextTest.test_storage_warning` | Aviso de pasta não protegida; os dois avisos juntos quando cabem |
| `DataPathsTest` (6 testes: `test_config_and_log_live_in_program_data`, `test_expand_only_system_drive`, `test_program_data_from_profile_list`, `test_system_folders_resolve`, `test_local_absolute`, `test_current_user_comes_from_the_token`) | Configuração em `<ProgramData>\Fervura`; só `%SystemDrive%` é expandida; valor do `ProfileList` com outra variável, relativo, UNC, `..` ou vazio = não resolvido (sem alternativa); pastas do Windows e Fonts resolvem; `is_local_absolute` recusa relativo, UNC, `..`, `%`, `C:` e `\\?\`; usuário do token ignora `USERNAME`/`USERDOMAIN` |
| `TrayFontTest` (4 testes: `test_absolute_font_path`, `test_fallback_to_default_font`, `test_without_fonts_dir_uses_default_font`, `test_fonts_dir_ignores_windir`) | Fonte da bandeja por caminho absoluto (`segoeuib.ttf`, depois `arialbd.ttf`, depois a padrão do Pillow); sem pasta de fontes, nunca tenta nome solto; `WINDIR`/`SystemRoot` ignorados |
| `ParseVersionTest` (8 testes: `test_accepted_versions`, `test_old_or_invalid_versions`, `test_padding_and_prefix`, `test_only_ascii_digits`, `test_huge_numbers_give_none_without_exception`, `test_odd_registry_values`, `test_pawnio_version_never_raises`, `test_pawnio_ok_uses_parsed_version`) | Versão do PawnIO: `2`, `2.0`, `2.0.0-beta` aceitos, `1.9.9` recusado; partes ausentes = 0; prefixo numérico; só dígitos ASCII; mais de 9 dígitos por parte = `None`; valores estranhos nunca lançam exceção |
| `IntervalArgTest.test_valid_values` | `--interval` padrão 1; aceita 2 e 0,1 |
| `IntervalArgTest.test_invalid_values_are_rejected` | `0`, `-1`, `0.05`, `nan`, `inf`, `-inf`, `abc` e vazio recusados pelo `argparse` (`SystemExit`) |
| `LoggingSetupTest.test_log_file_rotates` | `RotatingFileHandler` em `log.txt`, 1.000.000 bytes, 3 cópias, UTF-8 |
| `LoggingSetupTest.test_no_data_folder_means_no_log_file` | Sem pasta de dados, nenhum arquivo de log é criado |
| `LoggingSetupTest.test_hard_linked_log_or_rotation_disables_file_log` | `log.txt` ou `log.txt.3` em hard link: sem log em arquivo, alvo intacto |
| `DisplayLogTest.test_busy_port_logged_once_until_success` | "could not open COMx: …" uma vez por estado; volta a aparecer depois de uma conexão |
| `DisplayLogTest.test_different_error_is_logged` | Erro de abertura diferente gera nova linha |
| `DisplayLogTest.test_display_not_found_logged_once` | "display not found (VID:PID 1F3A:0008, 1A86:484A)" uma vez só |

`tests/test_datadir.py`:

| Teste | Garante |
|---|---|
| `ConstantsTest.test_data_dir_is_program_data` | `DATA_DIR` = `<ProgramData>\Fervura`; `config.json` dentro dela (só Windows) |
| `ConstantsTest.test_no_data_dir_outside_windows` | Fora do Windows não há pasta de dados (pulado no Windows) |
| `ConstantsTest.test_accepted_parent_owners` | Donos aceitos da pasta-mãe: SISTEMA, TrustedInstaller, Administradores |
| `ConstantsTest.test_descriptor_matches_the_required_permissions` | SDDL `O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)`; donos confiáveis Administradores e SISTEMA |
| `ConstantsTest.test_not_windows_is_refused` | `prepare()` recusa fora do Windows (pulado no Windows) |
| `PathWithoutEnvironmentTest` (4 testes: `test_real_value`, `test_environment_is_ignored_in_process`, `test_environment_is_ignored_by_a_new_process`, `test_unresolved_means_no_data_folder`) | `SystemDrive`, `ProgramData`, `ALLUSERSPROFILE` e `WINDIR` alterados (no processo e num processo novo) não mudam a pasta de dados nem a de fontes; caminho não resolvido = sem pasta, sem alternativa, `prepare()` recusa |
| `SafeFileTest` (4 testes: `test_missing_and_plain_files_are_safe`, `test_folder_is_not_a_safe_file`, `test_hard_link_is_unsafe`, `test_junction_is_unsafe`) | `is_safe_file`: ausente ou comum = seguro; pasta, hard link (os dois nomes) e junção = inseguro |
| `PrepareTest.test_creates_protected_folder` | Cria a pasta protegida (sem herança, SISTEMA e Administradores com controle total); arquivos novos herdam essas permissões |
| `PrepareTest.test_repairs_existing_folder` | Pasta existente com herança é reparada; arquivo antigo fica com as mesmas permissões de um novo; idempotente |
| `PrepareTest.test_refuses_folder_of_untrusted_owner` | Pasta de dono não confiável recusada e intocada (pulado rodando elevado) |
| `PrepareTest.test_refuses_junction_and_leaves_target_alone` | Junção no lugar da pasta recusada; alvo e permissões intactos; junção não apagada |
| `PrepareTest.test_refuses_file_in_place_of_folder` | Arquivo no lugar da pasta recusado e intacto |
| `PrepareTest.test_missing_parent_is_reported` | Pasta-mãe inexistente: recusa, nada criado |
| `PrepareTest.test_refuses_parent_of_untrusted_owner` | Pasta-mãe de dono não confiável: recusa citando o dono, nada criado (pulado rodando elevado) |
| `PrepareTest.test_refuses_junction_above_the_folder` | Junção na pasta-mãe ou mais acima: recusa, nada criado no alvo |
| `PrepareTest.test_refuses_relative_or_network_path` | Caminho relativo, UNC ou com `..` recusado |
| `PrepareTest.test_explicit_aces_of_existing_files_are_removed` | Arquivos já existentes com ACE explícita (Todos: controle total) ou sem herança ficam só com ACEs herdadas; conteúdo intacto |
| `PrepareTest.test_files_get_the_owner_of_the_descriptor` | Arquivos existentes recebem o dono do descritor |
| `PrepareTest.test_hard_linked_file_and_subfolder_are_left_alone` | Arquivo em hard link e subpasta não têm as permissões alteradas; alvo externo intacto |
| `PrepareTest.test_real_descriptor` | Descritor real: sem elevação recusa e não cria nada; elevado, dono Administradores e Usuários só leitura |

`tests/test_updates.py` (44 testes):

| Teste | Garante |
|---|---|
| `VersionTest` (6 testes: `test_accepted_tags`, `test_invalid_tags`, `test_unicode_digits_are_refused`, `test_too_many_digits_without_exception`, `test_non_text_tags`, `test_numeric_comparison`) | Tag `vX.Y.Z` ou `X.Y.Z` aceita; sufixos, partes a mais ou a menos, dígitos de outros alfabetos, mais de 9 dígitos e tipos não texto → `None` sem exceção; comparação numérica (1.10.0 > 1.9.0); tag inválida = sem atualização |
| `SingleVersionTest` (4 testes: `test_version_format`, `test_build_passes_the_version_to_inno_setup`, `test_installer_takes_the_version_from_the_build`, `test_build_runs_every_test_file`) | `__version__` no formato `X.Y.Z`, usada pelo app, pela comparação e pelo `User-Agent`; `build.bat` em CRLF passa `/DAppVersion=%APPVER%`; `.iss` com BOM e CRLF, só o `#ifndef` de padrão, sem versão fixa, `OutputBaseFilename` com a versão e AppId inalterado; o build roda todos os `tests/test_*.py` |
| `PageUrlTest` (3 testes: `test_urls_come_from_the_repo_constant`, `test_release_page_of_the_repo_is_accepted`, `test_anything_else_falls_back_to_latest_page`) | URLs derivadas de `UPDATE_REPO`; páginas do repositório aceitas; `http`, outro dono, sufixo no nome, outro host, `..`, `%2e%2e`, espaço, aspas, `\n`, `&`, mais de 2048 caracteres, maiúsculas e tipos errados → `.../releases/latest` |
| `ParseReleaseTest` (7 testes: `test_valid_answer`, `test_unusable_answers_raise_value_error`, `test_non_object_json`, `test_larger_than_limit`, `test_missing_or_wrong_fields_give_no_update`, `test_bad_html_url_uses_latest_page`, `test_draft_and_prerelease_are_ignored`) | Resposta válida; JSON inválido, aninhado demais ou não objeto → `ValueError` (sem `RecursionError`); acima de 1 MiB recusada; campos ausentes ou de tipo errado = sem atualização; `html_url` ruim → página `latest`; `draft`/`prerelease` diferentes de `false` ignorados |
| `CheckOnceTest` (6 testes: `test_request_is_https_with_generic_headers`, `test_newer_version_is_published`, `test_reads_at_most_the_limit`, `test_network_errors_never_raise`, `test_failures_logged_once_per_state`, `test_same_result_logged_once`) | `GET` HTTPS em `API_URL` com exatamente os 3 cabeçalhos genéricos e timeout de 10 s; versão maior publicada e logada; leitura de no máximo 1 MiB + 1; HTTP 404/403/500, rede, timeout e resposta inválida nunca lançam exceção nem chegam ao usuário; falhas e resultado logados uma vez por estado ("working again" ao voltar) |
| `SchedulingTest` (5 testes: `test_disabled_makes_no_request`, `test_turning_on_checks_right_away`, `test_first_check_after_delay_then_daily`, `test_no_request_before_first_delay`, `test_stop_ends_the_thread`) | Desligado: nenhuma requisição; ligar verifica na hora e desligar/religar não repete antes do intervalo mínimo; primeira verificação após o atraso (30 s) e depois a cada 24 h; nada antes do atraso; `stop()` encerra a thread *daemon* |
| `TransportTest` (2 testes: `test_redirect_only_to_the_github_api`, `test_certificates_verified_with_the_windows_store_only`) | Redirecionamento só para `https://api.github.com/`; no Windows, contexto TLS sem `create_default_context` (ignora `SSL_CERT_FILE`), `CERT_REQUIRED`, conferência do host e certificados carregados (só Windows) |
| `ShellExecTest` (5 testes: `test_safe_urls`, `test_unsafe_url_is_never_executed`, `test_safe_url_goes_to_the_desktop_shell`, `test_failure_is_reported_not_raised`, `test_no_elevated_fallback_in_the_code`) | `is_safe_url` recusa `http`, `file:`, espaço, aspas, `\n`, `%`, barra vertical e mais de 2048 caracteres; URL insegura nunca chega ao COM; URL segura vai ao Explorer; falha informada sem exceção; o código não usa `webbrowser`, `startfile`, `ShellExecuteW`, `ShellExecuteExW`, `explorer.exe` nem `subprocess`, e usa `CLSCTX_LOCAL_SERVER` |
| `AppUpdateTest` (5 testes: `test_update_text_and_tray_tip`, `test_result_shown_only_with_option_on`, `test_turning_off_hides_the_notice_and_saves`, `test_open_failure_shows_copy_link`, `test_copy_link`) | Texto "Nova versão X.Y.Z disponível" e dica da bandeja (até 127 caracteres); resultado mostrado só com a opção ligada (resposta tardia ignorada); desligar esconde o aviso e salva `check_updates: false`; falha ao abrir mostra "… — não foi possível abrir o navegador; use Copiar link" e a janela; Copiar link coloca a URL na área de transferência |
| `TrayUpdateItemTest.test_menu_item_follows_the_notice` | Item "Nova versão X.Y.Z disponível" no menu da bandeja visível só com versão nova, com esse texto; a ação chama `on_update` (abrir a página) |

**Regra:** toda correção de bug que puder ser testada sem hardware deve ganhar um teste aqui.

---

## 2. Preparação dos testes manuais

### 2.1 Ambientes

Cubra, sempre que possível:

| Variável | Valores |
|---|---|
| Windows | 10 (≥ 1809) e 11 |
| Controle de Aplicativo Inteligente (Win 11) | ligado e desligado |
| CPU | Intel e AMD |
| GPU | NVIDIA, AMD, só integrada, sem GPU dedicada |
| Python na máquina | sem Python (máquina limpa) e com Python |
| PawnIO | ausente, presente (≥ 2.0), versão antiga (< 2.0), se disponível |
| Display | conectado, desconectado |

Os testes de **temperatura da CPU, administrador e display** precisam de **máquina física**:
uma VM não expõe os sensores reais nem o dispositivo USB.

### 2.2 Ferramentas

- **Agendador de Tarefas:** `taskschd.msc`.
- **Registro:** `regedit`. PawnIO em
  `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO`.
- **Pasta de dados do Fervura:** `C:\ProgramData\Fervura` (por máquina, a mesma para todos os
  usuários e também no código-fonte). Contém `log.txt` (e `log.txt.1` a `.3`) e `config.json`.
  Ler não exige administrador; editar ou apagar arquivos, ou a pasta, exige um Prompt de Comando
  ou editor **como administrador**.
- **Permissões da pasta:** `icacls C:\ProgramData\Fervura` (permissões) e, no PowerShell,
  `(Get-Acl C:\ProgramData\Fervura).Owner` (dono).
- **Configuração de versões anteriores:** `%APPDATA%\Fervura\config.json` (só lida, na
  migração; nunca apagada).
- **Log do instalador:** `%TEMP%\Setup Log <data> #NNN.txt`.
- **Bloqueios de política:** Visualizador de Eventos → *Microsoft → Windows → CodeIntegrity → Operational*.
- **Conexões de rede** (aviso de atualização): Monitor de Recursos (`resmon`) → aba *Rede* →
  marcar `Fervura.exe` em *Processos com Atividade de Rede*; ou, num Prompt de Comando como
  administrador, `netstat -b -n 5` (procure linhas sob `[Fervura.exe]`).
- **Elevação e processo pai** (abertura do navegador): Gerenciador de Tarefas → *Detalhes* →
  clique direito no cabeçalho → *Selecionar colunas* → *Elevado*. Processo pai, no PowerShell:
  `Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" | Select-Object ProcessId, ParentProcessId`
  (troque pelo executável do navegador padrão) e
  `Get-Process explorer | Select-Object Id`.
- **Referência de temperatura:** o aplicativo LibreHardwareMonitor ou o HWiNFO, para comparar valores.
- **Portas seriais virtuais** (testes de protocolo sem o display): **com0com**, que cria um par
  de portas ligadas entre si (ex.: `COM8` ↔ `COM9`). O Fervura escreve em `COM8` (`--port COM8`)
  e um script lê de `COM9` e decodifica os pacotes:

  ```python
  # captura.py: rodar com Python + pyserial, na pasta do projeto
  import serial
  from fervura.protocol import FRAME_SIZE, parse_frame

  with serial.Serial("COM9", 1_000_000, timeout=5) as s:
      while True:
          frame = s.read(FRAME_SIZE)
          if len(frame) == FRAME_SIZE:
              readings, fah = parse_frame(frame)
              print(frame.hex(" "), "-> exibido:", readings.cpu_temp, "fahrenheit:", fah)
  ```

  Portas virtuais não têm VID/PID, então a descoberta automática não as encontra: use `--port`.

### 2.3 Executar com argumentos

O `Fervura.exe` não tem console. Para passar argumentos, use um Prompt de Comando (o UAC será pedido):

```
"C:\Program Files\Fervura\Fervura.exe" --port COM8 --interval 2 -v
```

O resultado dos testes aparece na janela e no `C:\ProgramData\Fervura\log.txt`. Argumento
inválido (ex.: `--interval 0`) faz o executável terminar logo depois do UAC, sem mensagem
(`INI-12`).

---

## 3. Teste rápido (antes de cada distribuição)

Na máquina física, com o display conectado:

1. [ ] `build.bat` termina sem `=== FALHOU` (inclui os automáticos).
2. [ ] Instalar por cima da versão anterior: não pede o PawnIO de novo e mantém a fonte escolhida
   (vinda de uma versão que gravava em `%APPDATA%`, a fonte é migrada na primeira execução; `CFG-06`).
3. [ ] Abrir pelo menu Iniciar: UAC aparece, janela abre, temperatura da CPU aparece.
4. [ ] O display mostra o mesmo número da janela e do ícone; o ícone fica **vermelho**.
5. [ ] Trocar a fonte (CPU/GPU/Maior) muda o número em até ~1 s.
6. [ ] Desconectar e reconectar o display: ícone cinza, depois vermelho de novo, sem reiniciar.
7. [ ] "Iniciar com o Windows" ligado → sair e entrar na conta → ícone aparece sem UAC.
8. [ ] Sair pelo menu da bandeja encerra o processo em até ~3 s (Gerenciador de Tarefas).
9. [ ] A janela **não** mostra o aviso laranja "A pasta %ProgramData%\Fervura não pôde ser
   protegida…"; `C:\ProgramData\Fervura\log.txt` existe e recebe as linhas desta execução,
   começando por "Fervura <versão> iniciando; administrador=True" com a versão do instalador.
10. [ ] "Verificar atualizações" aparece desmarcada numa instalação nova (ou atualizada a partir de
   uma versão sem a opção), e nenhum aviso de versão nova é exibido (`UPD-01`).

---

## 4. Casos de teste completos

Formato: **pré-condição → passos → resultado esperado**.

### 4.1 Build (`BLD`)

| ID | Caso | Passos | Esperado |
|---|---|---|---|
| BLD-01 | Build completo | `build.bat` em máquina com Python 3.12 e Inno Setup | Termina com `=== Pronto`; imprime `Versao: X.Y.Z` (o `__version__` de `fervura\__init__.py`); gera `dist\Fervura\Fervura.exe` e `dist\Fervura-Setup-X.Y.Z.exe` com essa versão (Propriedades → Detalhes → *Versão do produto*) |
| BLD-02 | Python ausente | `set PY=py -3.99` e `build.bat` | Para com "Python nao encontrado…" e a dica `py install 3.12` |
| BLD-03 | Teste automático falhando | Quebrar de propósito um teste e rodar `build.bat` | Para na etapa "Testes" com `=== FALHOU`; nenhum instalador novo |
| BLD-04 | Hash do PawnIO | Trocar um caractere de `PAWNIO_SHA256`, apagar `build\vendor\pawnio` e rodar o build | Para com "SHA-256 do PawnIO_setup.exe não confere" |
| BLD-05 | Dependências .NET | Após o build, listar `build\vendor\lhm` | 13 DLLs, incluindo `LibreHardwareMonitorLib.dll`, `HidSharp.dll`, `System.Memory.dll`; nenhuma `System.Management.dll` (vem do .NET Framework) |
| BLD-06 | Lista de componentes | Abrir `build\componentes.txt` | 4 seções: programa (citando administrador), Python com versão e cada biblioteca com versão e licença, LHM e dependências com versão e licença, PawnIO 2.2.0 como terceiro |
| BLD-07 | Avisos de terceiros | Abrir `build\THIRD_PARTY_NOTICES.txt` | Todas as bibliotecas com licença e URL; PawnIO com SHA-256 |
| BLD-08 | Manifesto de administrador | Propriedades do `dist\Fervura\Fervura.exe`; o ícone tem o escudo do UAC | Abrir pede UAC |
| BLD-09 | Ícone | Ver `Fervura.exe` e o instalador no Explorer | Ícone vermelho do Fervura |
| BLD-10 | Versão vinda do código | Mudar `__version__` em `fervura\__init__.py` para `1.2.3` e rodar o build; depois para `1.2`, `1.2.3-dev` e `x`. Desfazer ao final | Com `1.2.3`: `Versao: 1.2.3`, `dist\Fervura-Setup-1.2.3.exe` com versão do produto 1.2.3 e mesmo AppId. Com os outros: para antes do Inno Setup com "Versao invalida ou ausente em fervura\__init__.py: use __version__ = "X.Y.Z"" e `=== FALHOU`; nenhum instalador novo |
| BLD-11 | `.iss` compilado à mão | Depois de um build, rodar `ISCC.exe installer\Fervura.iss` sem `/D`; depois com `/DAppVersion=1.2.3` | Sem `/D`: `dist\Fervura-Setup-0.0.0-dev.exe`. Com `/D`: `dist\Fervura-Setup-1.2.3.exe`. Apagar os dois depois |

### 4.2 Instalador (`INS`)

| ID | Caso | Pré-condição | Passos | Esperado |
|---|---|---|---|---|
| INS-01 | Requisitos | Windows 10 ≥ 1809 x64 | Executar o instalador | UAC pedido; assistente em português |
| INS-02 | Lista de componentes | — | Avançar até a tela de informações | Conteúdo igual a BLD-06 |
| INS-03 | Recusar o PawnIO | PawnIO ausente | Avançar e clicar *Instalar*; responder **Não** | Instalação para com "Não é possível continuar a instalação sem o driver PawnIO."; nada copiado para `Program Files\Fervura` |
| INS-04 | Aceitar o PawnIO | PawnIO ausente | Igual, responder **Sim** | Página "Driver PawnIO" com progresso (extraindo → executando `-install` → verificando); PawnIO aparece em *Aplicativos* e no registro com versão ≥ 2.0; instalação continua |
| INS-05 | PawnIO já presente | PawnIO ≥ 2.0 | Instalar | **Nenhuma** pergunta sobre o PawnIO; log do instalador: "PawnIO x já está instalado" |
| INS-06 | PawnIO antigo | PawnIO < 2.0 (se disponível) | Instalar | Pergunta sobre o PawnIO (tratado como ausente) |
| INS-07 | Falha do PawnIO | Cancelar o instalador do PawnIO, se ele mostrar janela | Aceitar e cancelar | Instalação para com "O driver PawnIO não foi instalado (código N)…" |
| INS-08 | Arquivos | Após instalar | Ver `C:\Program Files\Fervura` | `Fervura.exe`, `_internal\`, `third_party\PawnIO\PawnIO_setup.exe` (mesmo SHA-256 do oficial), `THIRD_PARTY_NOTICES.txt` |
| INS-09 | Atalhos | Instalar com e sem "ícone na área de trabalho" | — | Menu Iniciar sempre; área de trabalho só quando marcado |
| INS-10 | Não abre ao final | — | Concluir | Página final **sem** opção de abrir o Fervura; nenhum erro 4551 |
| INS-11 | Programa aberto | Fervura em execução | Rodar o instalador | O instalador detecta o programa (`AppMutex`) e pede para fechá-lo antes de continuar |
| INS-12 | Atualização | Versão anterior instalada, fonte = GPU, autostart ligado | Instalar a nova e abrir pelo mesmo usuário | Sem pergunta do PawnIO; fonte continua GPU (migrada de `%APPDATA%` se a versão anterior gravava lá, ver `CFG-06`); tarefa `Fervura` continua existindo; vindo de uma versão sem a opção, "Verificar atualizações" desmarcada; *Aplicativos* mostra a versão nova |
| INS-13 | Máquina sem Python | VM/PC limpo | Instalar e abrir | Funciona sem instalar Python ou pacotes |

### 4.3 Inicialização do programa (`INI`)

| ID | Caso | Passos | Esperado |
|---|---|---|---|
| INI-01 | UAC | Abrir pelo menu Iniciar | UAC pedido; após aceitar, janela abre |
| INI-02 | UAC recusado (executável) | Abrir e recusar o UAC | O Windows não inicia o programa; nenhuma mensagem do Fervura |
| INI-03 | UAC recusado (código-fonte) | Anotar o tamanho e a data de `C:\ProgramData\Fervura\log.txt` (ou que a pasta não existe); `python iniciar.pyw` sem administrador; recusar | Mensagem "O Fervura precisa de permissão de administrador para ler a temperatura da CPU."; o console mostra "data folder not used (…)"; **nada gravado em disco** pelo processo não elevado: pasta não criada se não existia, `log.txt` e `config.json` inalterados |
| INI-04 | Instância única | Com o Fervura aberto, abrir de novo | "O Fervura já está em execução. Use o ícone na bandeja do sistema."; só um ícone na bandeja |
| INI-05 | Instância única minimizada | Com o Fervura aberto, rodar `Fervura.exe --minimized` | Sai em silêncio |
| INI-06 | PawnIO removido, recusar | Desinstalar o PawnIO; abrir o Fervura; responder Não | "Não é possível continuar sem o driver PawnIO." e fecha |
| INI-07 | PawnIO removido, aceitar | Igual; responder Sim | Instalador do PawnIO roda; Fervura abre com temperatura da CPU |
| INI-08 | Instalador do PawnIO ausente | PawnIO desinstalado e `third_party\PawnIO` apagado; abrir; Sim | "Instalador do PawnIO não encontrado… Reinstale o Fervura." e fecha |
| INI-09 | Biblioteca de sensores ausente | Renomear `_internal\lhm\LibreHardwareMonitorLib.dll`; abrir | "Não foi possível carregar a leitura de sensores: …" e fecha; detalhe no log. Desfazer depois |
| INI-10 | `--minimized` | Rodar com `--minimized` | Só o ícone na bandeja, sem janela |
| INI-11 | Log de início | Abrir e ver `C:\ProgramData\Fervura\log.txt` | Linhas "Fervura <versão> iniciando; administrador=True" (ex.: "Fervura 1.0.0 iniciando; administrador=True", com a versão do instalador), "PawnIO x.y.z instalado", temperaturas por hardware; **nenhuma** linha "data folder not used"; nada novo em `%APPDATA%\Fervura` |
| INI-12 | `--interval` inválido | Num Prompt de Comando: `"C:\Program Files\Fervura\Fervura.exe" --interval 0` (aceitar o UAC); repetir com `0.05`, `-1`, `nan`, `abc`. No código-fonte: `python iniciar.pyw --interval 0` e `echo %ERRORLEVEL%` | Executável: termina sem janela, sem ícone e sem mensagem; nenhuma linha nova no `log.txt`. Código-fonte: erro do `argparse` no console ("use um número de segundos maior ou igual a 0.1" ou "número inválido: …"), código de saída 2, **sem** pedir UAC. `--interval 0.1` é aceito |

### 4.4 Sensores (`SEN`) — máquina física

| ID | Caso | Esperado |
|---|---|---|
| SEN-01 | Temperatura da CPU | Valor plausível e próximo (±2 °C) do LibreHardwareMonitor/HWiNFO para o sensor *Package* (Intel) ou *Tctl/Tdie* (AMD) |
| SEN-02 | Temperatura da GPU | Próxima do sensor *GPU Core* (NVIDIA/Intel) ou *Edge* (AMD) na referência |
| SEN-03 | Uso, clock e memória da GPU | Valores coerentes com a referência; memória no formato `usada / total MB` |
| SEN-04 | Uso e clock da CPU, RAM | Coerentes com o Gerenciador de Tarefas |
| SEN-05 | Nomes | Nome da CPU e da GPU corretos |
| SEN-06 | GPU dedicada + integrada | A dedicada é a mostrada |
| SEN-07 | Sem GPU dedicada | GPU integrada mostrada; sem GPU suportada, campos da GPU com `—` |
| SEN-08 | Variação | Carga pesada na CPU (ex.: compactar um arquivo grande) faz a temperatura e o uso subirem na janela |
| SEN-09 | Temperatura indisponível | Rodando pelo código-fonte sem elevação (forçando o teste), ou em VM: campo `—`, aviso laranja "Temperatura da CPU indisponível. Veja o log.txt em %ProgramData%\Fervura.", **nunca** "0 °C". Sem elevação a pasta de dados também é recusada, então aparece junto, em outra linha, o aviso "A pasta %ProgramData%\Fervura não pôde ser protegida: a configuração não é salva e não há log.txt." |
| SEN-10 | Erro ao ler os sensores | Se for possível provocar uma exceção na leitura (ex.: falha da LHM durante a execução; caso coberto pelo teste automático `SenderTest.test_read_error_publishes_disconnected_update`): número `--`, campos `—`, status "erro ao ler sensores; veja o log.txt", ícone cinza; nada enviado; no log, um só traceback "send loop error" para erros iguais seguidos e "send loop recovered" quando a leitura volta |

### 4.5 Regras de exibição (`DSP`)

| ID | Caso | Passos | Esperado |
|---|---|---|---|
| DSP-01 | Fonte CPU | Selecionar "Temperatura da CPU" | Número grande = temperatura da CPU arredondada |
| DSP-02 | Fonte GPU | Selecionar "Temperatura da GPU" | Número = temperatura da GPU |
| DSP-03 | Maior das duas | Selecionar "Maior das duas" | Número = maior entre CPU e GPU |
| DSP-04 | Fallback | Fonte GPU numa máquina sem GPU suportada | Número = CPU; status "… — indisponível; mostrando a temperatura da CPU" |
| DSP-05 | Sem temperatura | VM sem sensores | `--`; status "sem temperatura para enviar"; nada enviado (confirmar com com0com) |
| DSP-06 | Limite de 99 | Coberto pelo teste automático `SenderTest` | — |
| DSP-07 | Arredondamento | Comparar janela e referência | 54,6 °C → 55 |

### 4.6 Serial e display (`SER`)

| ID | Caso | Passos | Esperado |
|---|---|---|---|
| SER-01 | Detecção automática | Display conectado; abrir | Status "conectado em COMx"; ícone vermelho; display mostra o número |
| SER-02 | Segundo VID/PID | Display com `1A86:484A`, se disponível | Detectado igual ao SER-01 |
| SER-03 | Sem display | Abrir sem o display e esperar 1 minuto | Status "display não encontrado, tentando de novo…"; ícone cinza; janela funciona; `log.txt` com **uma** linha "display not found (VID:PID 1F3A:0008, 1A86:484A)", não uma por segundo |
| SER-04 | Reconexão | Com tudo funcionando, desconectar o USB e reconectar | Cinza ao desconectar; vermelho em poucos segundos após reconectar, sem reiniciar |
| SER-05 | Porta ocupada | Abrir antes outro programa que use a porta do display (ex.: um monitor serial); esperar ~30 s; fechar o outro | Status "display não encontrado…"; log com **uma** linha "could not open COMx: …" (não uma a cada tentativa); ao fechar o outro, conecta sozinho ("connected on COMx"). Ocupar a porta de novo gera nova linha "could not open" |
| SER-06 | Pacote correto | com0com + `captura.py`; `--port COM8` | Pacotes de 23 bytes; `exibido` = número da janela; byte 1 = 0; demais campos 0 |
| SER-07 | Frequência | Igual, com `--interval 2`; depois com `--interval 0.1` (mínimo aceito) | Um pacote a cada ~2 s; com 0,1, pacotes bem mais frequentes (a pausa de 0,1 s soma-se ao tempo de leitura dos sensores), sem travar a janela. Valores abaixo de 0,1 são recusados (`INI-12`) |
| SER-08 | Simulação | `--dry-run -v` | Status "simulação (nada é enviado)"; `C:\ProgramData\Fervura\log.txt` com `frame 74 00 …` a cada ciclo; nenhuma porta aberta |
| SER-09 | Porta fixa | Display em COMx; `--port COMx` | Conecta sem procurar por VID/PID |
| SER-10 | Valor no display | Comparar display, janela e ícone por 1 minuto | Sempre o mesmo número |

### 4.7 Janela (`UI`)

| ID | Caso | Esperado |
|---|---|---|
| UI-01 | Layout | Seções Display, Sistema e Opções; título "Fervura"; tamanho fixo. Opções: "Iniciar com o Windows", botões Ocultar e Sair e, abaixo, a caixa "Verificar atualizações" (desmarcada por padrão). Sem versão nova, nenhuma linha de aviso nem os botões "Abrir página de download"/"Copiar link"; quando o aviso aparece (`UPD-04`), a janela cresce na altura e os textos não ficam cortados |
| UI-02 | Atualização | Valores mudam a cada ~1 s sem travar a janela |
| UI-03 | Fechar no X | Janela some; ícone continua; display continua recebendo |
| UI-04 | Ocultar | Igual ao UI-03 |
| UI-05 | Sair | Janela e ícone somem; processo `Fervura.exe` termina em até ~3 s (Gerenciador de Tarefas); sem linha "sender did not stop within 3 s" no log em uso normal |
| UI-06 | Valores ausentes | Campos sem dado mostram `—` |
| UI-07 | Avisos em laranja | Ver SEN-09: texto "Temperatura da CPU indisponível. Veja o log.txt em %ProgramData%\Fervura." e, em modo seguro (`DAD-02`), "A pasta %ProgramData%\Fervura não pôde ser protegida: a configuração não é salva e não há log.txt.", em linhas separadas quando aparecem juntos |

### 4.8 Bandeja (`TRY`)

| ID | Caso | Esperado |
|---|---|---|
| TRY-01 | Ícone | Número atual em branco, em negrito (Segoe UI Bold de `C:\Windows\Fonts`), sobre fundo vermelho (conectado) ou cinza (desconectado); `--` sem temperatura ou com erro de leitura dos sensores |
| TRY-02 | Dica | Passar o mouse: "Fervura: N" + status, em linhas separadas. Com versão nova (`UPD-04`), a linha "Nova versão X.Y.Z disponível" entre as duas; some ao desligar a opção (`UPD-07`) |
| TRY-03 | Clique esquerdo | Abre/traz a janela para frente |
| TRY-04 | Menu | Abrir; CPU/GPU/Maior com a atual marcada; Sair. Com versão nova (`UPD-04`), o item "Nova versão X.Y.Z disponível" logo abaixo de Abrir, que abre a página da versão (`UPD-05`); sem versão nova, o item não aparece |
| TRY-05 | Trocar fonte pelo menu | Número muda; opção correspondente marcada na janela (se aberta) |
| TRY-06 | Trocar fonte pela janela | Menu da bandeja reflete a nova escolha |
| TRY-07 | Sair pelo menu | Igual ao UI-05 (processo termina em até ~3 s) |

### 4.9 Configuração (`CFG`)

| ID | Caso | Esperado |
|---|---|---|
Arquivo: `C:\ProgramData\Fervura\config.json`. Editar ou apagar exige um editor ou Prompt de
Comando **como administrador**; feche o Fervura antes de editar.

| ID | Caso | Esperado |
|---|---|---|
| CFG-01 | Persistência | Escolher GPU, sair, abrir: GPU continua selecionada; `C:\ProgramData\Fervura\config.json` com conteúdo equivalente a `{"source": "gpu", "check_updates": false}` (UTF-8 sem BOM; as duas chaves sempre presentes); entrar com outro usuário do Windows e abrir: também GPU (configuração por máquina) |
| CFG-02 | Arquivo corrompido ou JSON não-objeto | Escrever lixo no `config.json`; abrir: fonte CPU (padrão), sem erro. Repetir com `[]`, `null`, `"gpu"` e `123`: fonte CPU. Salvar `{"source": "gpu"}` pelo Bloco de Notas como "UTF-8 com BOM": fonte **GPU** (BOM aceito) |
| CFG-03 | Valor inválido | `{"source": "xyz"}`, `{"source": 1}` e `{"source": ["gpu"]}`; abrir: fonte CPU |
| CFG-04 | Sem pasta | Como administrador, apagar `C:\ProgramData\Fervura` (e renomear `%APPDATA%\Fervura\config.json`, se existir, para não haver migração); abrir: pasta recriada com `log.txt`; fonte CPU; sem aviso laranja de pasta. `icacls C:\ProgramData\Fervura` mostra só `NT AUTHORITY\SISTEMA:(OI)(CI)(F)`, `BUILTIN\Administradores:(OI)(CI)(F)` e `BUILTIN\Usuários:(OI)(CI)(RX)`, nenhuma com `(I)` (sem herança); `(Get-Acl C:\ProgramData\Fervura).Owner` = `BUILTIN\Administradores`. Desfazer a renomeação depois |
| CFG-05 | Arquivo aninhado, grande ou com chave desconhecida | (a) `config.json` com 100.000 `[` seguidos: fonte CPU, sem erro. (b) arquivo com mais de 64 KiB (ex.: `{"source": "gpu", "pad": "<70.000 letras x>"}`): fonte CPU. (c) `{"source": "gpu", "extra": 1}`: fonte GPU; depois de trocar a fonte para Maior, o arquivo contém só `source` e `check_updates` (`"extra"` sumiu) |
| CFG-06 | Migração de `%APPDATA%` | Fechar o Fervura; como administrador, apagar `C:\ProgramData\Fervura\config.json`; criar `%APPDATA%\Fervura\config.json` do seu usuário com `{"source": "gpu", "check_updates": true, "x": 1}`; abrir: fonte GPU e "Verificar atualizações" **desmarcada** (`check_updates` não é migrado); `C:\ProgramData\Fervura\config.json` criado só com `source` e `"check_updates": false`; `log.txt` com "settings migrated from …"; o arquivo em `%APPDATA%` continua lá, sem alteração. Abrir de novo com `%APPDATA%` dizendo `max`: continua GPU (o arquivo novo prevalece) |
| CFG-07 | Erro ao salvar | Com o Fervura aberto, num Prompt de Comando como administrador: `attrib +r C:\ProgramData\Fervura\config.json` (o arquivo precisa existir: troque a fonte uma vez antes); trocar a fonte: muda na sessão (display e janela); status com " — configuração não salva: <erro>", mantido nas atualizações seguintes; `log.txt` com "settings not saved: …"; o arquivo não muda. Depois `attrib -r C:\ProgramData\Fervura\config.json` e trocar a fonte de novo: a mensagem some e o arquivo é gravado. (Apagar a pasta inteira com o programa aberto costuma falhar porque o `log.txt` está em uso.) |

### 4.10 Iniciar com o Windows (`AUT`)

| ID | Caso | Passos | Esperado |
|---|---|---|---|
| AUT-01 | Ativar | Marcar a caixa | Tarefa `Fervura` no Agendador: gatilho "Ao fazer logon" do usuário atual com atraso de 10 s, "Executar com privilégios mais altos", ação `Fervura.exe --minimized`, sem limite de tempo, inicia na bateria. Usuário do gatilho e de "Ao executar a tarefa, usar a conta de usuário" = `COMPUTADOR\usuário` (ou `DOMÍNIO\usuário`) da conta logada, igual a `whoami`. Nenhum `autostart-task.xml` sobra em `C:\ProgramData\Fervura` |
| AUT-02 | Logon | Sair e entrar na conta (ou reiniciar) | ~10 s depois, ícone na bandeja **sem UAC** e sem janela; display recebendo |
| AUT-03 | Estado da caixa | Fechar e abrir o Fervura | Caixa marcada se a tarefa existe; desmarcada se não |
| AUT-04 | Desativar | Desmarcar | Tarefa some do Agendador; próximo logon não inicia o Fervura |
| AUT-05 | Desinstalação | Com a caixa marcada, desinstalar | Tarefa `Fervura` removida |
| AUT-06 | Instância duplicada | Autostart ligado e o usuário abre manualmente após o logon | Mensagem de "já está em execução" (INI-04) |
| AUT-07 | Falha | Simular erro (ex.: Agendador desativado por política) | Status "não foi possível alterar a inicialização: …"; caixa volta ao estado real. Em modo seguro, ver `DAD-02` |
| AUT-08 | Bloqueio pelo Controle de Aplicativo Inteligente | Controle de Aplicativo Inteligente ligado (Win 11); autostart ligado; sair e entrar na conta | Registrar se o Fervura inicia. Se não iniciar, anotar no relatório os eventos 3033/3077 em *CodeIntegrity → Operational* (problema conhecido, seção 5; não é falha do Fervura) |

### 4.11 Desinstalação (`DES`)

| ID | Caso | Esperado |
|---|---|---|
| DES-01 | Desinstalar por *Aplicativos* | `C:\Program Files\Fervura` removida; atalhos removidos |
| DES-02 | Programa aberto | Desinstalador detecta o Fervura aberto e pede para fechá-lo |
| DES-03 | Tarefa | Ver AUT-05 |
| DES-04 | O que permanece | `C:\ProgramData\Fervura` (configuração e log, com as mesmas permissões), o `%APPDATA%\Fervura` de versões anteriores (se houver) e o PawnIO continuam |
| DES-05 | Reinstalação | Reinstalar após desinstalar: sem pergunta do PawnIO; configuração anterior recuperada de `C:\ProgramData\Fervura\config.json`; sem aviso laranja de pasta |

### 4.12 Segurança e licenças (`SEC`)

Executados pela revisão de segurança (agente Luana), não pelos testes de funcionalidade.

| ID | Caso | Esperado |
|---|---|---|
| SEC-01 | Sem WinRing0 | Nenhum arquivo `.sys` nem `WinRing0*` na pasta instalada |
| SEC-02 | PawnIO íntegro | SHA-256 de `third_party\PawnIO\PawnIO_setup.exe` = `1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032` (`certutil -hashfile <arquivo> SHA256`), conferido em `build\vendor\pawnio\` e na pasta instalada (`C:\Program Files\Fervura\third_party\PawnIO\`) |
| SEC-03 | Avisos | `THIRD_PARTY_NOTICES.txt` presente e completo em `build\` e na pasta instalada (`C:\Program Files\Fervura\`) |
| SEC-04 | Controle de Aplicativo Inteligente ligado | Registrar se o instalador, o programa (pelo menu Iniciar) e a tarefa de autostart abrem. Havendo bloqueio (ex.: erro 4551), registrar os eventos 3033/3077 de *CodeIntegrity → Operational* (arquivo bloqueado e política, ex.: `0283ac0f-fff1-49ae-ada1-8a933130cad6`). Problema conhecido de executável sem assinatura (seção 5), não reprova a versão |
| SEC-05 | Dependências do pacote | Em `dist\Fervura\_internal` (e em `C:\Program Files\Fervura\_internal`): Pillow ≥ 13 (pasta `pillow-<versão>.dist-info` ou `.venv-build\Scripts\pip show Pillow`); **nenhuma** pasta ou arquivo `setuptools*` nem `pkg_resources*`. Na pasta instalada, conferir depois de reinstalar a versão testada: uma instalação antiga pode ainda ter `_internal\setuptools` |
| SEC-06 | Navegador aberto sem elevação | (a) `Fervura.exe` instalado, aberto pelo menu Iniciar (elevado), com o aviso de versão nova visível (`UPD-04`); fechar antes todas as janelas do navegador padrão (com o navegador já aberto, a página só vira uma aba do processo existente e o teste não conclui nada). Clicar "Abrir página de download"; repetir pelo item do menu da bandeja. Conferir no Gerenciador de Tarefas → *Detalhes* a coluna *Elevado* do navegador e, no PowerShell (2.2), o processo pai. (b) **Altera o sistema: só com autorização explícita do usuário.** Com o Explorer da área de trabalho elevado ou o UAC desligado, repetir | (a) O navegador abre na página da versão com *Elevado* = **Não**, e o processo pai é o `explorer.exe` da área de trabalho, não o `Fervura.exe`. (b) **Nada abre**; a linha passa a "Nova versão X.Y.Z disponível — não foi possível abrir o navegador; use Copiar link"; `log.txt` com "could not open <url>: o Explorer da área de trabalho está elevado; o navegador também ficaria" (ou outro motivo); em nenhum caso surge um navegador elevado |

### 4.13 Identificação e compatibilidade do display (`CMP`)

Procedimentos da `specification.md` 4.1 e 4.3. Os comandos rodam no PowerShell; não precisam
de administrador.

Comando A, procura os VID/PID suportados:

```powershell
Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -match 'VID_1A86&PID_484A|VID_1F3A&PID_0008' } | Select-Object Name, PNPDeviceID
```

Comando B, lista todas as portas seriais:

```powershell
Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match '\(COM\d+\)' } | Select-Object Name, PNPDeviceID
```

| ID | Caso | Passos | Esperado |
|---|---|---|---|
| CMP-01 | Display reconhecido por comando | Display conectado; rodar o comando A | Uma linha com `(COMx)` e `USB\VID_1A86&PID_484A\...` ou `USB\VID_1F3A&PID_0008\...` |
| CMP-02 | Display desconectado | Desconectar; rodar o comando A; reconectar e rodar de novo | Sem linhas; depois a mesma linha volta com o mesmo `COMx` (mesma entrada USB) |
| CMP-03 | Achar a porta do display | Rodar o comando B com o display conectado e desconectado | A linha que some e volta é a do display; o `COMx` bate com o do CMP-01 |
| CMP-04 | Gerenciador de Dispositivos | *Portas (COM e LPT)* → porta do display → *Propriedades* → *Detalhes* | *IDs de hardware* com um dos pares suportados; anotar a *Descrição do dispositivo relatada pelo barramento* no registro do teste |
| CMP-05 | Status coerente | Abrir o Fervura | Status `conectado em COMx` com o mesmo número do CMP-01; número aparece no display |
| CMP-06 | Número da porta trocado | Fechar o Fervura; mudar o número no Gerenciador de Dispositivos (*Configurações de Porta → Avançado*) para um livre; reconectar e abrir o Fervura | Conecta sozinho; status mostra o **novo** número; nenhuma configuração necessária |
| CMP-07 | Outra entrada USB | Ligar o display em outra entrada USB e abrir o Fervura | Conecta, mesmo que o Windows tenha dado outro número de porta |
| CMP-08 | Porta forçada inexistente | `Fervura.exe --port COM99`; deixar rodar ~30 s | Status "display não encontrado, tentando de novo…"; log com **uma** linha "could not open COM99: …", não uma a cada tentativa; sem travar |
| CMP-09 | Outro adaptador USB-serial | Display desconectado e outro adaptador serial conectado (VID/PID diferente) | Fervura **não** conecta nele: status "display não encontrado…"; comando A sem linhas |
| CMP-10 | Display com VID/PID diferente, mesmo protocolo | Se disponível: `Fervura.exe --port COMx` na porta dele | Número aparece no display. Registrar o VID/PID para inclusão (CMP-11) |
| CMP-11 | Inclusão de novo VID/PID | Acrescentar o par em `DEVICE_IDS` (`fervura/protocol.py`) e na tabela 4.1 da especificação; gerar nova versão | Testes automáticos passam; display novo conectado **sem** `--port` (repetir CMP-01 a CMP-05 com o par novo); displays antigos continuam funcionando |
| CMP-12 | Display de outro protocolo | Se disponível: `--port COMx` na porta dele | Número **não** aparece. Resultado esperado: o display não é suportado; registrar, não é falha |

### 4.14 Pasta de dados, log e modo seguro (`DAD`)

Comportamento da `specification.md` 7.3. Quase todos exigem um Prompt de Comando **como
administrador** para preparar o cenário e uma **conta de usuário comum** (sem administrador)
quando indicado. Feche o Fervura antes de cada preparação e, ao final, desfaça o cenário
apagando como administrador o que foi criado e abrindo o Fervura de novo (a pasta é recriada).

| ID | Caso | Passos | Esperado |
|---|---|---|---|
| DAD-01 | Log rotativo | Com o Fervura fechado, como administrador, aumentar o `log.txt` para mais de 1 MB (ex.: no PowerShell como administrador, `Add-Content C:\ProgramData\Fervura\log.txt ('x' * 1100000)`); abrir o Fervura e esperar alguns segundos. Repetir 4 vezes | Surge `log.txt.1` com o conteúdo antigo e um `log.txt` novo e pequeno; nas repetições, `.1` → `.2` → `.3`; **nunca** existe `log.txt.4`; cada arquivo com no máximo ~1 MB |
| DAD-02 | Pasta plantada por usuário comum (modo seguro) | Em modo normal, deixar "Iniciar com o Windows" marcado e fechar o Fervura. Como administrador, apagar `C:\ProgramData\Fervura`. Entrar numa conta **comum** e criar `C:\ProgramData\Fervura` (`mkdir`). Voltar à conta de administrador e abrir o Fervura (a caixa aparece marcada). Trocar a fonte; desmarcar a caixa; marcar de novo; sair e reabrir | Fervura abre e envia ao display normalmente; aviso laranja "A pasta %ProgramData%\Fervura não pôde ser protegida: a configuração não é salva e não há log.txt."; nada gravado na pasta plantada (nem `log.txt` nem `config.json`) e suas permissões não mudam; a troca de fonte vale na sessão, e ao reabrir a fonte é CPU; desmarcar funciona (a tarefa some do Agendador); marcar mostra "não foi possível alterar a inicialização: pasta de dados protegida indisponível (C:\ProgramData\Fervura); veja o log" e a caixa volta a desmarcada. Apagar a pasta como administrador e reabrir: modo normal |
| DAD-03 | Junção no lugar da pasta | Como administrador, apagar `C:\ProgramData\Fervura`; criar `C:\alvo` com um arquivo `teste.txt`; `mklink /J C:\ProgramData\Fervura C:\alvo`; anotar `icacls C:\alvo`; abrir o Fervura | Modo seguro (aviso laranja, como no DAD-02); `C:\alvo` e `teste.txt` intactos, com as mesmas permissões (`icacls`); nenhum `log.txt` ou `config.json` em `C:\alvo`; a junção **não** é apagada nem movida |
| DAD-04 | Permissões para usuário comum | Em modo normal, entrar numa conta **comum** e tentar: ler `C:\ProgramData\Fervura\log.txt`; criar `C:\ProgramData\Fervura\x.txt`; editar `config.json`; apagar `log.txt` | Leitura funciona; criar, editar e apagar são negados ("Acesso negado") |
| DAD-05 | Hard link no log | Fechar o Fervura. Como administrador: criar `C:\alvo\alvo.txt` com o texto `original`; anotar `icacls C:\alvo\alvo.txt`; apagar `C:\ProgramData\Fervura\log.txt`; `mklink /H C:\ProgramData\Fervura\log.txt C:\alvo\alvo.txt`; abrir o Fervura, trocar a fonte, sair | Fervura funciona sem log em arquivo: `alvo.txt` continua só com `original` e com as mesmas permissões; a configuração continua sendo salva (`config.json` atualizado); **sem** aviso laranja de pasta (só o log está desativado). Repetir com `log.txt.3` no lugar de `log.txt`: mesmo resultado. Desfazer apagando o link |
| DAD-06 | Arquivo antigo com permissão explícita | Fechar o Fervura. Como administrador: `icacls C:\ProgramData\Fervura\config.json /grant *S-1-1-0:F` (Todos: controle total); conferir com `icacls` a linha `Todos:(F)` sem `(I)`; abrir o Fervura | Depois de abrir, `icacls C:\ProgramData\Fervura\config.json` mostra só permissões herdadas (todas com `(I)`), sem `Todos`; dono `BUILTIN\Administradores`; conteúdo do arquivo intacto |
| DAD-07 | Variáveis de ambiente do usuário alteradas | **Altera o registro: só com autorização explícita do usuário.** Na conta de teste, em `HKCU\Environment`, criar `SystemDrive` = `C:\Users\<usuário>\x` e `ProgramData` = `C:\Users\<usuário>\x\ProgramData` (pasta criada antes); sair e entrar na conta; abrir o Fervura pelo menu Iniciar (UAC); trocar a fonte; com autostart ligado, sair e entrar na conta | Log e `config.json` continuam em `C:\ProgramData\Fervura` (aberto pelo UAC e pela tarefa agendada); nada criado em `C:\Users\<usuário>\x`; sem aviso laranja de pasta. Desfazer: apagar os dois valores de `HKCU\Environment` e sair/entrar na conta |
| DAD-08 | Pasta existente com permissões alteradas é reparada | Com o Fervura fechado, como administrador: `icacls C:\ProgramData\Fervura /inheritance:e` (reativa herança); abrir o Fervura | Modo normal; `icacls C:\ProgramData\Fervura` volta ao estado do `CFG-04` (sem `(I)`, só SISTEMA, Administradores e Usuários) |

### 4.15 Aviso de atualização (`UPD`)

Comportamento da `specification.md` 7.5. O repositório consultado é o de `UPDATE_REPO`
(`fervura/updates.py`). Enquanto ele não tiver release publicada, a API responde 404: só
`UPD-01`, `UPD-02`, `UPD-03`, `UPD-08`, `UPD-09` e `UPD-10` podem ser feitos; `UPD-04` a `UPD-07`
e `SEC-06` precisam de uma release com tag maior que a versão instalada (ex.: `v1.0.1` com a
1.0.0 instalada). A conferência de que o navegador abre sem elevação está em `SEC-06`.

| ID | Caso | Passos | Esperado |
|---|---|---|---|
| UPD-01 | Desligada por padrão, sem conexão | Instalação nova (ou `config.json` sem `check_updates`); abrir o Fervura; observar a rede (2.2) por pelo menos 2 minutos | Caixa "Verificar atualizações" desmarcada; **nenhuma** conexão de `Fervura.exe` no Monitor de Recursos / `netstat -b`; `log.txt` sem linhas "update check"; nenhum aviso de versão nova |
| UPD-02 | Ligar sem release no repositório | Repositório sem release (ou inexistente); marcar "Verificar atualizações"; esperar ~1 minuto; desmarcar e marcar de novo | Uma conexão a `api.github.com` logo ao marcar; `log.txt` com **uma** linha "update check failed: HTTP 404", sem repetição ao religar; nada aparece na janela nem na bandeja; `config.json` com `"check_updates": true` |
| UPD-03 | Ligada desde o início | Com a opção marcada, sair e abrir o Fervura | A primeira verificação acontece cerca de 30 s depois da linha "Fervura <versão> iniciando", e não antes: a conexão aparece no Monitor de Recursos e o `log.txt` ganha nesse momento a linha do resultado ("update check failed: HTTP 404", "no update available (current X.Y.Z)" ou "update available: …") |
| UPD-04 | Aviso de versão nova | Release com tag maior que a versão instalada; marcar a opção (ou abrir com ela marcada e esperar ~30 s) | Linha azul "Nova versão X.Y.Z disponível" sob as opções, com os botões "Abrir página de download" e "Copiar link"; dica da bandeja com a linha da versão (`TRY-02`); item no menu da bandeja (`TRY-04`); `log.txt` com **uma** linha "update available: X.Y.Z (https://github.com/<UPDATE_REPO>/releases/…)". Nada é baixado: nenhum arquivo novo em `%TEMP%`, `Downloads` ou na pasta do programa |
| UPD-05 | Abrir a página | Com o aviso visível, clicar "Abrir página de download"; depois usar o item "Nova versão X.Y.Z disponível" do menu da bandeja | O navegador padrão abre a página da release no GitHub (`https://github.com/<UPDATE_REPO>/releases/...`), sem travar a janela; nada é executado além do navegador. Elevação conferida no `SEC-06` |
| UPD-06 | Falha ao abrir → "use Copiar link" | Com o aviso visível, encerrar o Windows Explorer (Gerenciador de Tarefas → *Processos* → *Windows Explorer* → *Finalizar tarefa*); clicar "Abrir página de download"; clicar "Copiar link" e colar no Bloco de Notas. Restaurar o Explorer depois (*Executar nova tarefa* → `explorer.exe`, **sem** marcar "Criar esta tarefa com privilégios administrativos") | Nada abre; a linha passa a "Nova versão X.Y.Z disponível — não foi possível abrir o navegador; use Copiar link" e a janela é exibida; `log.txt` com "could not open <url>: <erro>"; o texto colado é a URL da página da release |
| UPD-07 | Desligar esconde e persiste | Com o aviso visível, desmarcar "Verificar atualizações"; sair e abrir de novo | Ao desmarcar, a linha e os botões somem, o item some do menu da bandeja e a dica perde a linha da versão; `config.json` com `"check_updates": false`; ao reabrir, caixa desmarcada, sem aviso e sem conexão (como `UPD-01`) |
| UPD-08 | Sem rede | Desligar a rede (Wi-Fi/cabo); marcar a opção; esperar ~1 minuto; religar a rede; desmarcar e marcar de novo depois de mais de 60 s | Sem mensagem na janela; `log.txt` com **uma** linha "update check failed: <motivo>"; depois de religar e remarcar, "update check working again" e o resultado ("no update available (current X.Y.Z)", "update available: …" ou outra falha, como o HTTP 404 do `UPD-02`) |
| UPD-09 | Valor não booleano no arquivo | Fechar o Fervura; como administrador, gravar `{"source": "gpu", "check_updates": 1}` no `config.json`; abrir; repetir com `"true"` e `null`; depois trocar a fonte | Fonte GPU; caixa desmarcada e nenhuma conexão (como `UPD-01`); depois da troca de fonte, o arquivo tem `"check_updates": false`. Com `true` (JSON), a caixa abre marcada |
| UPD-10 | Modo seguro | No cenário do `DAD-02`, marcar "Verificar atualizações"; sair e reabrir | A caixa marca e vale na sessão (conexão a `api.github.com` no Monitor de Recursos); nada gravado na pasta plantada; ao reabrir, caixa desmarcada |

---

## 5. Problemas conhecidos (não são falhas de teste)

- SmartScreen mostra "Editor desconhecido": executáveis não assinados.
- Erro 4551 e outros bloqueios: a causa provável é o **Controle de Aplicativo Inteligente**
  bloqueando o executável sem assinatura (eventos CodeIntegrity 3033/3077), não o fato de ser
  aberto pelo instalador; não confirmado. Pode bloquear também a tarefa agendada de "Iniciar com
  o Windows" (`AUT-08`). Por isso o instalador não abre o programa ao final. Registre os eventos
  (`SEC-04`).
- Modo seguro quando `C:\ProgramData\Fervura` foi criada antes por um usuário comum, ou está no
  lugar dela uma junção ou um arquivo: o Fervura funciona, mas sem salvar a configuração e sem
  log, com aviso laranja na janela (`DAD-02`). Solução: um administrador apaga o que estiver no
  lugar da pasta e reabre o Fervura.
- A configuração é por máquina: trocar a fonte numa conta muda também para as outras contas.
- Em VM, a temperatura da CPU e o display não funcionam (ver 2.1).
- O instalador do PawnIO pode ou não mostrar janela própria (registrar no INS-04).
- Enquanto o repositório de `UPDATE_REPO` não tiver release, a verificação de atualizações
  registra "update check failed: HTTP 404" no log (`UPD-02`). Não é falha.
- Com o Explorer da área de trabalho elevado ou o UAC desligado, "Abrir página de download" não
  abre nada, de propósito: use "Copiar link" (`SEC-06`).

---

## 6. Modelo de registro

```
Versão testada:        Fervura x.y.z   (instalador: Fervura-Setup-x.y.z.exe)
Data / testador:
Ambiente:              Windows __ build ____ | CPU ________ | GPU ________
                       Smart App Control: ligado/desligado | PawnIO antes: ausente/x.y.z
                       Display: conectado (VID:PID ____) / ausente
                       Pasta de dados: modo normal / modo seguro (motivo no log ou no console)

ID        Resultado (OK / FALHA / N/A)   Observação
BLD-01
INS-03
...

Falhas encontradas (ID, o que aconteceu, log anexo):
```
