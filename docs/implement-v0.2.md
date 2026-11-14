# hostbook v0.2: tài liệu thực thi

Tài liệu này đủ chi tiết để phiên sau chỉ việc làm theo. Phần thảo luận gốc nằm ở [ke-hoach-v0.2.md](ke-hoach-v0.2.md). Mọi điểm còn mở đã được chốt ở mục 1; các giả định kỹ thuật đã được chạy thử ở mục 2.

Làm theo bốn mốc, mỗi mốc phát hành được riêng: **M1** kho secret và đăng nhập → **M2** gợi ý thông minh → **M3** chia sẻ, nhập, kiểm tra kết nối → **M4** giao diện và theme. Mỗi mốc kết thúc khi bộ kiểm thử đạt trên macOS và Ubuntu (CI), README đã cập nhật, và đã thử tay các màn mới trong terminal thật (qua tmux, như các lần trước).

## Trạng thái thực thi (14/11/2026)

- **M1–M3:** đã có trong nhánh chính, đến commit `f8fe0fd` (v0.3.0).
- **M4:** đã triển khai v0.4.0 trên nhánh `feat/m4-interface`: cấu hình riêng từng máy, theme, ba kiểu danh sách, màn Giao diện có xem trước, chọn giao diện lần đầu và giữ kiểu gọn khi nâng cấp. Đang xác nhận CI trước khi đưa vào nhánh chính.
- Review độc lập đã phát hiện và xử lý các ca: đóng/mở preview phải tính lại bề rộng danh sách; Nerd Font trên Bash 3.2; màu scalar có tab phải bị từ chối; nhãn prod phải luôn thấy dù tên/tag dài; demo phải phản ánh đường kẻ và cùng ngưỡng hiện ô chi tiết.
- CI macOS của M2/M3 bị treo ở tạo mã khôi phục do `tr | head` đọc vô hạn khi SIGPIPE bị bỏ qua. Đã thay bằng đọc hữu hạn từ `/dev/urandom`; kiểm thử mô phỏng điều kiện này, không thay đổi định dạng mã khôi phục hay secret hiện có.

## 1. Các quyết định đã chốt

| Việc | Chốt |
|---|---|
| Mã hoá | `age` ≥ 1.3 (có sẵn plugin `batchpass`). Kho secret dùng một danh tính age; mật khẩu chính chỉ khoá danh tính đó |
| Mở khoá | Danh tính được nhớ trong **Keychain** (macOS) hoặc secret-service (Linux). Mỗi máy nhập mật khẩu chính một lần |
| Quên mật khẩu chính | Có **mã khôi phục**, hiện một lần lúc tạo kho secret |
| Nơi giữ key | Chỉ trong kho secret. Không còn lựa chọn trỏ tới file key trên máy |
| Secret | Mục có tên, dùng lại cho nhiều server; key chống trùng theo fingerprint |
| ssh ngoài app | Config ghim file `.pub`; key thật nằm trong ssh-agent sau khi mở khoá (`hb unlock`) |
| Chia sẻ | Gói chữ mã hoá bằng mật khẩu dùng một lần; phạm vi = kết quả lọc; **mặc định kèm secret**; không cần kết nối hai máy |
| Nhập gói | Màn xem trước, so trùng từng server; sau khi nhập **hỏi** có kiểm tra kết nối không |
| Host key khi kiểm tra kết nối | `StrictHostKeyChecking=accept-new`: tự lưu host key của server chưa từng gặp, không bao giờ chấp nhận host key đã đổi |
| Giao diện | Dùng fzf; ba kiểu danh sách, nhiều theme, file cấu hình riêng từng máy (không đồng bộ) |
| Công cụ "Sao chép key từ máy khác" | **Bỏ**, vì kho secret thay thế. `ensure_key` (key để vào máy chứa kho lần đầu) giữ nguyên |
| Server không dùng secret | Vẫn cho phép một lựa chọn cuối danh sách: "Không lưu (ssh tự lo)" cho người dùng agent riêng, YubiKey… |

## 2. Kết quả chạy thử (đã kiểm chứng trên máy này, OpenSSH 10.5, age 1.3.2, fzf 0.75)

| Giả định | Kết quả |
|---|---|
| Mã hoá bằng mật khẩu không cần TTY | `AGE_PASSPHRASE=… age -e -j batchpass -a` và `age -d -j batchpass` chạy được; sai mật khẩu báo `incorrect passphrase`, mã thoát khác 0 |
| Thêm secret không cần mở khoá | `age -e -r "$(cat vault.pub)"` chỉ cần khoá công khai |
| Giải mã với danh tính qua stdin | `age -d -i - file.age < danh-tinh` chạy được, danh tính không cần nằm trên đĩa |
| Tốc độ scrypt | khoá và mở danh tính ≈ 0,46 giây |
| Nạp key vào agent không ghi file | `age -d … | ssh-add -q -t 3600 -` chạy được |
| Ghim key bằng `.pub` | `IdentityFile x.pub` + `IdentitiesOnly yes` + key trong agent: vào được, kể cả qua file ssh config thường. Không ghim mà agent có 8 key: `Too many authentication failures` |
| File `.pub` phải `chmod 600` | Quyền 644 thì ssh in cảnh báo "UNPROTECTED PRIVATE KEY FILE" khi agent chưa có key |
| Khi chưa mở khoá | ssh in `Load key "….pub": invalid format` rồi `Permission denied` → dùng để nhận ra "kho secret chưa mở" |
| Mật khẩu qua askpass | `SSH_ASKPASS=<script> SSH_ASKPASS_REQUIRE=force ssh …` đăng nhập được, không cần `sshpass` |
| Phát hiện key có passphrase | `ssh-keygen -y -P "" -f key` khác 0; gỡ bằng `ssh-keygen -p -P <cũ> -N "" -f key` |
| Keychain | `security add-generic-password -U -s … -a … -w "$(base64)"` rồi `find-generic-password -w | base64 -d` khớp. Phải bọc base64 vì danh tính có nhiều dòng |
| Socket agent | Đường dẫn socket dài quá ~100 ký tự thì `ssh-agent -a` lỗi → socket riêng phải đặt ở `/tmp/hb-XXXXXX/` |
| `.gitignore` dạng whitelist | Thêm `!secrets/`, `!secrets/*.age`, `!secrets/*.pub`, `!secrets/*.meta` thì đúng các file cần được theo dõi, file lạ trong `secrets/` bị bỏ qua |
| Phím chia sẻ | `--bind 'ctrl-y:execute-silent(touch MARK)+transform:[ "${FZF_SELECT_COUNT:-0}" -gt 0 ] && echo accept || echo select-all+accept'` trả về đúng các dòng đang khớp |
| Thẻ nhiều dòng | `fzf --read0` với `--delimiter '\t' --with-nth 2.. --accept-nth 1`: tìm được trên dòng 2 của thẻ, trả về khoá ở trường 1 |
| Theme theo terminal | `--color` nhận tên màu ANSI (`magenta`, `bright-black`…) và các nền `16`, `light`, `dark`, `bw` |
| Kiểm tra song song | 4 kết nối song song, một cái không tới được: tổng 5,0 giây với `ConnectTimeout=5` |
| age bản Linux | tarball chứa `age/age`, `age/age-keygen`, `age/age-plugin-batchpass` |

Chưa kiểm chứng, phải thử ngay khi làm (dùng container ở mục 8.1): với `SSH_ASKPASS_REQUIRE=force`, câu hỏi xác nhận host key cũng đi qua askpass; thiết kế ở 4.6 chuyển tiếp câu hỏi đó ra `/dev/tty`.

**Cạm bẫy đã gặp khi thử:** (1) chạy `bash <<EOF` mà trong đó có `ssh` thì ssh nuốt phần còn lại của script → mọi lệnh ssh không tương tác phải có `</dev/null`. (2) `eval "$(ssh-agent -a <đường dẫn dài>)"` thất bại im lặng rồi `ssh-add` nạp nhầm vào agent thật của người dùng → luôn kiểm tra `[ "$SSH_AUTH_SOCK" = "$sock" ]` trước khi `ssh-add`, và bộ kiểm thử **không bao giờ** dùng agent thật (mục 8.1).

## 3. Thay đổi nền, làm trước M1

### 3.1 Phụ thuộc và trình cài

- `check_deps`: thêm `age` ≥ 1.3.0 (`age --version` in `v1.3.2`) và `age-plugin-batchpass` có trong PATH. Thiếu thì báo như fzf/gum.
- `install.sh`: thêm `age`. macOS có brew: `brew install age`. Còn lại: tải `https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-<os>-<arch>.tar.gz`, kiểm sha256 ghim sẵn, rồi cài `age`, `age-keygen`, `age-plugin-batchpass` vào `~/.local/bin`:

  ```
  cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10  age-v1.3.2-linux-amd64.tar.gz
  6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4  age-v1.3.2-linux-arm64.tar.gz
  e2020b073c44f692685a24d6abc378817eb81ffaaf49fd0531ef8565f767f2f5  age-v1.3.2-darwin-arm64.tar.gz
  1d1e4bc66e1427edad7739ae7616157de0e79db8b6d2a1497d7d9925fb06a539  age-v1.3.2-darwin-amd64.tar.gz
  ```
  `fetch_bin` hiện nhận URL file checksum; thêm biến thể nhận thẳng sha256 và danh sách nhiều file cần lấy ra từ tarball.
- `VERSION=0.2.0` khi xong M1.

### 3.2 Bố cục file

```
~/ssh-hosts/                       (đồng bộ qua kho)
  <dự án>.conf   groups.tsv   .gitattributes   .gitignore
  secrets/
    vault.pub                      khoá công khai age của kho secret (1 dòng)
    vault.key.age                  danh tính age, khoá bằng mật khẩu chính
    vault.recovery.age             danh tính age, khoá bằng mã khôi phục
    <tên>.age                      nội dung secret, mã hoá tới vault.pub, dạng armor (-a)
    <tên>.meta                     type=key|password, fp=, keytype=, created=
    <tên>.pub                      public key (chỉ với type=key), chmod 600
~/.config/hostbook/                (riêng từng máy, không đồng bộ; M4)
  config   themes/<tên>.theme
~/.local/state/hostbook/           (riêng từng máy)
  …như cũ…   tmp/ (0700)   askpass (0700)   agent.env   last-check.tsv   last-import
```

- `.gitignore` do `first_run` và `ensure_merge_setup` ghi: thêm bốn dòng `!secrets/`, `!secrets/*.age`, `!secrets/*.pub`, `!secrets/*.meta` (hàm `ensure_merge_setup` bổ sung nếu thiếu, kèm commit riêng như đang làm với `.gitattributes`).
- `.gitattributes`: thêm `secrets/* -merge` (git không tự trộn nội dung secret; xung đột xử lý ở 4.9).
- Tên secret: `^[a-z0-9][a-z0-9._-]{0,39}$`, không được là `vault`.

### 3.3 Server tham chiếu secret thế nào

Trong file `.conf`, hostbook ghi (hàm `host_block`, tham số thứ 6 đổi từ đường dẫn key sang **tên secret**):

```
# tags: prod web
# desc: Nginx chính
# secret: key-booking
Host a-web1
  HostName 10.0.1.11
  User ubuntu
  IdentityFile ~/ssh-hosts/secrets/key-booking.pub
  IdentitiesOnly yes
```

Với secret là mật khẩu:

```
# secret: matkhau-deploy
Host b-api
  HostName 10.2.0.5
  User deploy
  PubkeyAuthentication no
  PreferredAuthentications password,keyboard-interactive
```

Không dùng secret: không ghi dòng `# secret:` và không ghi dòng đăng nhập nào.

- Đường dẫn `.pub` ghi theo `$DIR` thật, rút gọn `$HOME` thành `~`.
- **Secret của một server** = giá trị dòng `# secret:`; nếu không có thì suy ra từ `identityfile` hiệu lực (`ssh -G`) khi nó trỏ vào `secrets/<tên>.pub` (để khối wildcard cũng mang secret được).
- Chỉ mục (`index.tsv`) thêm **trường 12 = tên secret**. `index_file` đọc `# secret:` giống `# tags:`; phần awk sau `ssh -G` áp quy tắc suy ra ở trên. Trường 7 (đường dẫn key) giữ lại để nhận ra server kiểu cũ.
- `merge_driver` không cần đổi: dòng `#` đã thuộc về khối Host ngay dưới.
- Dòng danh sách (`cmd_list`) thêm vào phần đuôi ẩn: `secret:<tên>` để tìm được "server nào dùng secret này".
- `cmd_preview`: thay dòng `IdentityFile` bằng `Đăng nhập: key-booking (key ed25519)` hoặc `…(mật khẩu)` hoặc `ssh tự lo`.

### 3.4 Hàm tiện ích mới

```
tmpd()            → tạo và in thư mục tạm trong $STATE/tmp (0700); mọi nội dung secret dạng thường chỉ nằm ở đây; trap xoá khi thoát
clip_get()        → pbpaste | wl-paste | xclip -o -selection clipboard | xsel -ob
secret_meta n k   → đọc khoá k trong secrets/n.meta
secret_users n    → awk trên IDX: các server có trường 12 == n
valid_secret_name
```

## 4. M1: kho secret và đăng nhập

### 4.1 Danh tính và mở khoá

```
vault_exists()    [ -f "$DIR/secrets/vault.pub" ]
vault_id()        8 ký tự đầu của sha256(vault.pub)          # khoá tra cứu trong Keychain
kc_get/kc_set/kc_del  theo backend:
   macOS:  security find|add|delete-generic-password -s hostbook -a "vault:$(vault_id)"   (giá trị bọc base64)
   Linux có secret-tool: secret-tool lookup|store|clear service hostbook account "vault:<id>"
   Linux không có: file "${XDG_RUNTIME_DIR:-/dev/shm}/hostbook-$UID/identity-<id>" (0600, mất khi tắt máy);
                   không có cả hai thư mục đó thì không nhớ, hỏi mỗi lần
vault_identity()  # in danh tính ra stdout; trả 1 nếu người dùng huỷ
   1. id=$(kc_get); nếu có và  age-keygen -y <<<"$id" == vault.pub  → in, xong
      (không khớp: kho secret đã bị thay → kc_del, đi tiếp)
   2. hỏi "Mật khẩu chính của kho secret" (gum input --password), tối đa 3 lần;
      AGE_PASSPHRASE=$pw age -d -j batchpass secrets/vault.key.age → id; sai thì báo "Sai mật khẩu chính"
      sau 3 lần sai: hỏi "Dùng mã khôi phục?" → vault_recover
   3. kc_set "$id"; in id
```

Danh tính chỉ đi qua pipe/stdin (`age -d -i - file < <(vault_identity)`), không vào biến môi trường hay tham số lệnh. Đổi mật khẩu chính trên máy khác không ảnh hưởng máy này, vì Keychain giữ danh tính chứ không giữ mật khẩu.

### 4.2 Tạo kho secret (`vault_init`)

Gọi tự động lần đầu cần tới secret (thêm secret, nhập gói có secret), hoặc `hb vault init`.

```
‹ Tạo kho secret
Kho secret giữ key và mật khẩu đã mã hoá, đồng bộ qua kho cùng danh sách server.

Mật khẩu chính · ít nhất 10 ký tự
> ••••••••••••
Nhập lại
> ••••••••••••
✓ Đã tạo kho secret · máy này sẽ nhớ, không hỏi lại

╭──────────────────────────────────────────────────────────────╮
│ Mã khôi phục                                                  │
│ HB-7K3Q-9XTM-2WPL-R8DC-5VNA                                    │
│ Dùng khi quên mật khẩu chính. Cất vào trình quản lý mật khẩu   │
│ hoặc ghi ra giấy. Mã này chỉ hiện một lần.                     │
╰──────────────────────────────────────────────────────────────╯
> Đã cất, tiếp tục
  Copy mã vào clipboard
```

Các bước: (1) kiểm mật khẩu ≥ 10 ký tự và hai lần khớp; (2) `age-keygen` → danh tính, `age-keygen -y` → `vault.pub`; (3) `vault.key.age` = batchpass(mật khẩu chính); (4) mã khôi phục = 20 ký tự Crockford base32 (bảng `0123456789ABCDEFGHJKMNPQRSTVWXYZ`) lấy từ `/dev/urandom`, hiển thị `HB-XXXX-XXXX-XXXX-XXXX-XXXX`; mật khẩu batchpass là 20 ký tự bỏ gạch → `vault.recovery.age`; (5) `kc_set`; (6) commit "Tạo kho secret" + đồng bộ. Người dùng thoát giữa chừng ở bước hiện mã: kho đã tạo, in nhắc `hb vault recovery-code` để tạo mã mới.

Ngay sau đó chạy **chuyển key đang dùng** (4.3).

Lệnh phụ:
- `hb vault passwd`: cần danh tính (Keychain hoặc mật khẩu cũ) → hỏi mật khẩu mới hai lần → ghi lại `vault.key.age`.
- `hb vault recover`: hỏi mã khôi phục → giải `vault.recovery.age` → đặt mật khẩu chính mới → **tạo mã khôi phục mới** (mã cũ hết hiệu lực) và hiện.
- `hb vault recovery-code`: cần danh tính; tạo mã khôi phục mới.
- `hb lock [--forget]`: gỡ các key của hostbook khỏi agent (`ssh-add -d secrets/<tên>.pub`); `--forget` xoá thêm danh tính khỏi Keychain.
- `hb unlock`: nạp mọi key secret vào agent (4.5).

### 4.3 Chuyển key đang dùng vào kho secret

Sau `vault_init`, và có trong Công cụ → Secret khi còn server kiểu cũ:

```
Các server đang dùng key trên máy này:
  ~/.ssh/id_rsa       1 server
  ~/.ssh/id_rsa_dev   3 server
> Đưa vào kho secret (khuyên dùng)
  Để sau
```

Với mỗi đường dẫn key khác nhau ở trường 7 mà file tồn tại: `secret_add_key <đường dẫn>` (4.4, tên mặc định từ tên file), rồi viết lại config: mọi dòng `IdentityFile X` trong `*.conf` mà `X` mở rộng ra đúng đường dẫn đó → thay bằng `IdentityFile <secrets/tên.pub>`, đảm bảo trong cùng khối có `IdentitiesOnly yes`; với khối không phải wildcard, thêm `# secret: <tên>` ngay trên dòng Host nếu chưa có. Sau đó `validate` → commit "Đưa N key vào kho secret". File gốc trong `~/.ssh` không bị đụng tới. Key không đọc được hoặc sai định dạng: bỏ qua, báo tên.

### 4.4 Thêm secret

`secret_add_key <nguồn>` (nguồn = `clip` | đường dẫn | `gen`):

1. Lấy nội dung vào file trong `tmpd`. `clip`: `clip_get`; nếu không chứa `PRIVATE KEY` thì mở `$EDITOR` trên file trống có dòng hướng dẫn. `gen`: `ssh-keygen -q -t ed25519 -N "" -C "<tên>@hostbook"`.
2. Chuẩn hoá: bỏ `\r`, thêm xuống dòng cuối, `chmod 600`.
3. Kiểm tra: `ssh-keygen -y -P "" -f f` thành công → không có passphrase. Thất bại vì passphrase (thử `ssh-keygen -y -P "" …` báo `incorrect passphrase`) → hỏi passphrase (ẩn) và gỡ: `ssh-keygen -q -p -P <pp> -N "" -f f`. Thất bại khác → "Không phải private key hợp lệ", dừng.
4. `fp=$(ssh-keygen -lf f | awk '{print $2}')`, `keytype` từ trường cuối. Nếu có `.meta` nào `fp` trùng → in "Key này đã có tên <x>", trả về `<x>`, không tạo mới.
5. Tên: mặc định từ tên file (`id_rsa_dev` → `id-rsa-dev`) hoặc comment của key; hỏi bằng `ask` có điền sẵn; kiểm hợp lệ và chưa dùng.
6. Ghi `<tên>.pub` (`ssh-keygen -y`, chmod 600), `<tên>.meta`, `<tên>.age` (`age -e -a -r "$(cat vault.pub)"`). Xoá file tạm. Commit qua `save_and_sync "Đã thêm secret <tên>"`.

`secret_add_password`: hỏi tên, hỏi mật khẩu hai lần (ẩn), `.meta` có `type=password`, không có `.pub`.

### 4.5 Dùng key khi kết nối

```
agent_ready()     # đảm bảo có ssh-agent dùng được
   nếu ssh-add -l trả 0 hoặc 1 → dùng agent hiện có (macOS luôn có; ssh gõ tay và VS Code cũng thấy)
   ngược lại: đọc $STATE/agent.env; nếu agent đó còn sống thì dùng; không thì
              d=$(mktemp -d /tmp/hb-XXXXXX); eval "$(ssh-agent -a $d/a.sock)"; kiểm [ "$SSH_AUTH_SOCK" = "$d/a.sock" ]; ghi agent.env
load_secret n     # với type=key
   ssh-add -l | grep -qF "$(secret_meta n fp)" && return 0
   vault_identity | age -d -i - secrets/n.age | ssh-add -q -t "$KEY_TTL" -     (KEY_TTL mặc định 28800 = 8 giờ; cấu hình key_ttl)
secrets_for h     # secret của h và của mọi máy trung gian (đệ quy theo trường 6, tối đa 5 tầng)
```

`connect()` gọi trước khi chạy ssh: `agent_ready`; với mỗi secret trong `secrets_for h`: key → `load_secret`; password → chuẩn bị askpass (4.6). Người dùng huỷ ở bước nhập mật khẩu chính → in "Chưa mở kho secret nên chưa kết nối", quay lại. `open_many` làm việc này **một lần ở tiến trình cha** cho mọi server trước khi tạo ô tmux, để các ô không cùng hỏi.

`hb unlock`: `agent_ready`, rồi `load_secret` cho mọi secret kiểu key; in "Đã nạp N key, dùng được trong 8 giờ".

`explain()` bổ sung:
- nhật ký có `Load key ".*/secrets/.*\.pub": invalid format` và đăng nhập bị từ chối → loại `locked`: "Kho secret chưa mở trên máy này" → thao tác "Mở khoá và thử lại".
- `denied` trên server có secret key: dòng "Đã thử: user X, secret <tên>" và thêm thao tác "Copy public key của <tên>" (để thêm lên server).
- `denied` trên server có secret mật khẩu: "Mật khẩu đã lưu trong <tên> không đúng" → thao tác "Đổi mật khẩu của <tên>".

### 4.6 Đăng nhập bằng mật khẩu (askpass)

- `$STATE/askpass` là script hai dòng, tạo lúc cần: `#!/bin/sh` / `exec <SELF> _askpass "$@"`.
- `connect()` đặt cho ssh: `SSH_ASKPASS=$STATE/askpass SSH_ASKPASS_REQUIRE=force HB_ASKPASS_HOST=<tên> HB_ASKPASS_COUNT=<file đếm trong tmpd>`.
- `_askpass "$prompt"`:
  1. Prompt chứa `yes/no` (xác nhận host key) → in prompt ra `/dev/tty`, đọc trả lời từ `/dev/tty`, in ra stdout. Khi `HB_ASKPASS_NOTTY=1` (kiểm tra kết nối) → in rỗng.
  2. Prompt chứa `assword` → tìm secret: tách `user@máy` trong prompt, tra chỉ mục server có `hostname`==máy và `user`==user; không ra thì dùng secret của `HB_ASKPASS_HOST`. **Lần hỏi đầu** cho secret đó (theo file đếm): giải mã và in. **Lần sau** (mật khẩu đã lưu sai): hỏi người dùng trên `/dev/tty`, ẩn chữ, kèm dòng "Mật khẩu đã lưu không đúng"; `NOTTY` → in rỗng.
  3. Prompt khác (passphrase, mã OTP…) → chuyển tiếp ra `/dev/tty`, ẩn chữ.
- Kiểm chứng đầu tiên khi làm: câu hỏi host key đi qua nhánh 1 đúng như thiết kế. Nếu OpenSSH không đưa nó qua askpass thì bỏ nhánh 1, không ảnh hưởng phần còn lại.

### 4.7 Chọn cách đăng nhập khi thêm hoặc sửa server

Hàm `pick_login <ngữ cảnh gợi ý>` → in tên secret, hoặc rỗng (ssh tự lo), mã thoát 1 nếu huỷ:

```
Đăng nhập bằng
> key-booking        ●●● đang dùng cho 6 server booking, cùng dải 10.8.1.x
  key-dev            ●○○ đang dùng cho 3 server project-a
  matkhau-deploy     mật khẩu · 2 server
  + Dán key mới      (copy key trước rồi chọn mục này)
  + Nhập key từ file
  + Mật khẩu mới
  Không lưu          ssh tự lo: agent riêng, YubiKey…
```

- Thứ tự và phần lý do lấy từ bộ gợi ý (M1: secret phổ biến nhất trong dự án đã đoán đứng đầu; M2 thay bằng điểm số ở mục 5).
- "+ Nhập key từ file": `gum choose` các file trong `~/.ssh` có dòng đầu chứa `PRIVATE KEY`, cộng mục "Đường dẫn khác…" (`gum file`).
- Chưa có kho secret mà chọn mục cần kho → `vault_init` trước.
- `add_host`: hàng "Key" trong khung xem lại đổi thành "Đăng nhập"; mục "Sửa một mục…" gọi `pick_login`. Khi không có gợi ý nào đủ chắc (M1: dự án chưa có secret nào), `pick_login` được hỏi trong luồng, trước màn xem lại.
- `host_actions`: thêm "Đổi cách đăng nhập" → `pick_login` → `set_host_secret <server> <tên|rỗng>`: trong phạm vi `block_range`, xoá dòng `# secret:`, `IdentityFile`, `IdentitiesOnly`, và (chỉ khi khối đang có secret mật khẩu) `PubkeyAuthentication no`, `PreferredAuthentications …`; rồi chèn các dòng mới theo 3.3. Chạy qua `apply_change`.
- Server dùng chung một dòng `Host a b`: báo sửa bằng `^e`, như các thao tác khác.

### 4.8 Quản lý secret (Công cụ → Secret, hoặc `hb secret`)

Danh sách:

```
‹ Secret                                          kho secret: đã mở trên máy này
> key-booking        key ed25519 · 6 server · SHA256:egtk…DZgU
  key-dev            key rsa · 3 server
  matkhau-deploy     mật khẩu · 2 server
  key-cu             key rsa · không server nào dùng
  + Thêm key (từ clipboard)   + Thêm key từ file   + Tạo key mới   + Thêm mật khẩu
```

Thao tác trên một secret:

| Thao tác | Cách làm |
|---|---|
| Xem server đang dùng | về ô tìm với truy vấn `secret:<tên>` |
| Copy public key | `.pub` → clipboard (chỉ với key) |
| Thay bằng key khác | `secret_add_key` vào **cùng tên**; fingerprint trùng secret khác → từ chối; gỡ key cũ khỏi agent; sau đó hỏi "Đưa key mới lên N server đang dùng?" (dưới) |
| Tạo key mới thay thế | như trên với nguồn `gen` |
| Sửa nội dung | giải mã vào `tmpd` → `$EDITOR` → kiểm tra (key: bước 3–4 của 4.4; mật khẩu: một dòng, không rỗng) → mã hoá lại; không đổi thì thôi |
| Đổi mật khẩu | chỉ với mật khẩu; hỏi hai lần |
| Đổi tên | `git mv` ba file; trong `*.conf` thay `# secret: cũ` và đường dẫn `secrets/cũ.pub`; `validate`; một commit |
| Xoá | liệt kê server đang dùng; `confirm --default=false`; xoá file; trong các khối liên quan xoá dòng `# secret:` và các dòng đăng nhập do hostbook ghi; gỡ khỏi agent |

**Đưa key mới lên server** (tuỳ chọn sau khi thay key; làm cuối M1): với từng server dùng secret đó, máy trung gian trước:
1. Bằng **key cũ** (còn trong agent, ghim qua một bản `.pub` cũ để trong `tmpd`): `ssh <server> 'umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys' < <tên>.pub`
2. Thử key mới: `ssh -o BatchMode=yes -o IdentitiesOnly=yes -o IdentityFile=<tên>.pub <server> true`
3. Hỏi một lần cho cả lượt (mặc định **Không**): "Gỡ key cũ khỏi các server đã vào được bằng key mới?" → `ssh <server> "grep -vF '<phần base64 của key cũ>' ~/.ssh/authorized_keys > ~/.ssh/authorized_keys.hb && mv ~/.ssh/authorized_keys.hb ~/.ssh/authorized_keys"`
4. In bảng ✓/✗ từng server. Server lỗi vẫn giữ nguyên, chưa thay được gì thì nói rõ key mới chưa dùng được ở đó.

### 4.9 Đồng bộ và xung đột của secret

`commit_all` đã `git add -A` nên secret đi theo. Hai máy cùng sửa **một** secret → `pull --rebase` thất bại → trạng thái `conflict` như hiện có. `resolve_conflict` bổ sung: sau khi `git merge`, với mỗi đường dẫn chưa gộp trong `secrets/`: chế độ `ours`/`theirs` → `git checkout --ours|--theirs -- <đường dẫn>` rồi `git add`; chế độ sửa tay → hỏi từng **secret** (cả ba file `.age/.meta/.pub` lấy cùng một phía): "Giữ bản của máy này / Lấy bản trong kho".

### 4.10 `doctor`

Thêm: phiên bản age; kho secret (chưa tạo / đã mở trên máy này qua Keychain|secret-service|phiên tạm / chưa mở); số secret và số secret không server nào dùng; server tham chiếu secret không tồn tại (lỗi); server kiểu cũ còn trỏ file key (cảnh báo, gợi ý 4.3); `chmod 600` lại mọi `secrets/*.pub` có quyền khác. Bỏ dòng kiểm quyền các file key trong `~/.ssh`.

### 4.11 Lệnh và mục menu của M1

`hb unlock`, `hb lock [--forget]`, `hb secret`, `hb vault init|passwd|recover|recovery-code`. Công cụ: thêm "Secret", bỏ "Sao chép key từ máy khác". Trợ giúp: thêm mục "Secret". README: mục "Secret" và sửa mục "Bảo mật" (key giờ được đồng bộ nhưng đã mã hoá; ai có kho vẫn cần mật khẩu chính).

Hook kiểm thử: `_vault_init <mật khẩu>` (không hỏi, in mã khôi phục), `_secret_add_key <file> <tên>`, `_secret_add_password <tên> <mật khẩu>`, `_secret_get <tên>`, `_set_host_secret`, `_askpass`, biến `HOSTBOOK_KEYCHAIN=file:<đường dẫn>` để kiểm thử không đụng Keychain thật, `HOSTBOOK_MASTER=<mật khẩu>` để `vault_identity` không hỏi.

## 5. M2: gợi ý thông minh khi thêm server

Thay `infer`/`read_infer`/`name_prefix` bằng một chương trình awk `suggest` (hook `_suggest <địa chỉ> <tên> <user> <cổng>`), đọc `IDX` và `HIST`.

### 5.1 Điểm của từng server đã có (so với server đang thêm)

| Dấu hiệu | Điểm | Nhãn lý do |
|---|---|---|
| Cùng dải /24 (IPv4) | 50 | `cùng dải 10.8.1.x` |
| Cùng /16, khác /24 | 15 | `cùng dải 10.8.x.x` |
| Địa chỉ là tên miền, giống mọi nhãn trừ nhãn đầu | 55 | `cùng đuôi .booking.vn` |
| Tên miền, chỉ giống tên miền gốc (2 nhãn cuối; 3 nhãn nếu nhãn cuối dài 2 và nhãn kề ≤ 3 ký tự, ví dụ `com.vn`) | 40 | `cùng tên miền booking.vn` |
| Tên cùng tiền tố (phần trước dấu `-`, `.`, `_` hoặc chữ số đầu tiên) | 40 | `tên bắt đầu bằng bk-` |
| Mỗi từ chung khác trong tên (tách theo `-_.` và chữ số), tối đa 2 từ | 10 | `tên có chữ web` |
| Cùng cổng khác 22 | 10 | `cùng cổng 2222` |
| Cùng user (khi người dùng đã ghi user) | 10 | `cùng user deploy` |
| Dùng trong 7 ngày gần đây | 5 | (không hiện) |

Trong nhóm dải IP và nhóm tên miền, mỗi nhóm chỉ tính dấu hiệu cao nhất.

### 5.2 Điểm dự án và độ chắc

- Điểm dự án = tổng điểm của **3 server điểm cao nhất** trong dự án đó, chia 1,5 (để một server rất giống ≈ ba server giống vừa).
- Độ chắc: `●●●` ≥ 80 · `●●○` ≥ 45 · `●○○` ≥ 15 · dưới 15 không hiện.
- Lý do của dự án = các nhãn khác nhau của những server đóng góp, kèm số server: `cùng dải 10.8.1.x (4 server) · tên bắt đầu bằng bk- (5 server)`.
- **Tự chọn** khi điểm cao nhất ≥ 80 **và** hơn dự án thứ hai ≥ 30. Ngược lại người dùng phải chọn:

```
Dự án nào?
> booking      ●●●  cùng dải 10.8.1.x (4 server) · tên bắt đầu bằng bk- (5 server)
  project-a    ●○○  cùng dải 10.8.x.x (2 server)
  + Dự án mới
```

### 5.3 "Giống server nào nhất"

Server có điểm cao nhất, nếu ≥ 60:

```
Giống bk-api nhất (cùng dải 10.8.1.x, tên bắt đầu bằng bk-).
Sao chép thiết lập của bk-api? user duong · key-booking · tags #prod #api
> Có    Không
```

Đồng ý: lấy dự án, user (nếu địa chỉ dán vào không ghi user), máy trung gian, secret của server đó; tags lấy theo 5.4. Màn xem lại ghi `← giống bk-api` cho các ô này.

### 5.4 Giá trị từng ô khi không sao chép

- **User**: user phổ biến nhất trong các server của dự án đã chọn, tính trọng số theo điểm; không có thì user của máy.
- **Cổng**: cổng dán vào; không có thì 22.
- **Máy trung gian**: nếu ≥ 80% server của dự án (không tính server có tag `bastion`) cùng đi qua một máy thì chọn máy đó.
- **Secret**: phổ biến nhất trong các server của dự án có cùng user; không có thì phổ biến nhất của dự án; không có nữa thì để trống và `pick_login` được hỏi trong luồng.
- **Tags**: tag có ở ≥ 60% server của dự án, cộng các từ trong tên trùng với một tag đã tồn tại ở bất kỳ đâu. Khi sửa mục Tags: `gum choose --no-limit` gồm các tag gợi ý (đánh dấu sẵn) và mọi tag khác của dự án, thêm mục "+ Tag khác…" để gõ.
- **Tiền tố tên** (điền sẵn ở bước hỏi tên, trước khi có tên): tiền tố của server điểm cao nhất tính chỉ bằng dấu hiệu địa chỉ; nếu < 15 điểm thì không điền.

### 5.5 Luồng thêm server sau M2

1. Địa chỉ (dán) → 2. Tên (có tiền tố điền sẵn kèm lý do) → 3. nếu có ứng viên "giống nhất": hỏi sao chép → 4. nếu dự án chưa đủ chắc: chọn dự án → 5. nếu chưa có secret: `pick_login` → 6. màn xem lại, mỗi ô ghi nguồn → Lưu / Sửa một mục… / Huỷ.

Trường hợp chắc chắn: địa chỉ, tên, đồng ý sao chép, Lưu (4 lần enter). `import_write` (nhập nhiều dòng) dùng cùng `suggest` nhưng không hỏi: lấy dự án điểm cao nhất nếu ≥ 15, không thì `imported`.

Đầu ra của `suggest` (TSV, để kiểm thử được): dòng `project<TAB>tên<TAB>điểm<TAB>lý do`, `peer<TAB>tên<TAB>điểm<TAB>lý do`, `field<TAB>user|jump|secret|tags|prefix<TAB>giá trị<TAB>lý do`, `auto<TAB>project<TAB>0|1`.

## 6. M3: chia sẻ, nhập gói, kiểm tra kết nối

### 6.1 Tạo gói

Lối vào: `^y` ở ô tìm (các dòng đã chọn bằng `tab`; nếu không chọn gì thì **mọi dòng đang khớp**; binding ở mục 2, tệp đánh dấu `$STATE/share-request`), mục "Chia sẻ" trong menu `→` của nhiều server (6.5), và `hb share [truy vấn…]` (lọc bằng `fzf --filter`). Nhóm được chọn → lấy các server thành viên. Dòng phiên tmux bị bỏ qua.

Nội dung gói (thư mục trong `tmpd`, rồi `tar -czf -`):

```
manifest            version=1  from=<hostname -s>  date=<ISO>  hosts=N  secrets=M
conf/<dự án>.conf   các khối Host đã "làm phẳng"
groups.tsv          nhóm có ≥ 2 thành viên nằm trong gói, thành viên đã lọc theo gói
secrets/<tên>.key | <tên>.pass | <tên>.meta | <tên>.pub     nội dung THƯỜNG (cả gói sẽ được mã hoá)
```

- **Làm phẳng** từng server: sinh khối chuẩn bằng `host_block` từ chỉ mục (giá trị hiệu lực, nên thứ thừa hưởng từ wildcard không bị mất), rồi nối các dòng tuỳ chọn khác của khối gốc (những dòng không thuộc `HostName, User, Port, ProxyJump, IdentityFile, IdentitiesOnly, PubkeyAuthentication, PreferredAuthentications`).
- **Máy trung gian** của server trong gói mà chưa có trong gói → tự thêm vào, đánh dấu trong màn xem trước.
- Secret: chỉ những secret mà server trong gói dùng. Cần danh tính (`vault_identity`).
- Không kèm secret: bỏ thư mục `secrets/` và mọi dòng đăng nhập cùng `# secret:` trong các khối.

Màn xem trước:

```
‹ Chia sẻ
12 server · 2 nhóm · 2 secret (1 key, 1 mật khẩu)                    ● 3 server prod
  booking (7)     bk-api, bk-db, bk-web1, …
  project-a (5)   a-web1, a-web2, … + a-bastion (kèm vì là máy trung gian)
  Secret          key-booking, matkhau-deploy
> Tạo gói (kèm secret)
  Tạo gói không kèm secret
  Huỷ
```

Mã hoá và phát gói: mật khẩu gói = 4 từ ngẫu nhiên trong bảng 128 từ dưới đây + 2 chữ số, nối bằng `-` (ví dụ `cam-sao-bien-lua-73`, khoảng 34 bit, cộng scrypt); có mục "Tự đặt mật khẩu…" (≥ 8 ký tự). `tar … | AGE_PASSPHRASE=… age -e -j batchpass -a`. Văn bản gói:

```
# hostbook-share v1 · 12 server · từ Mac
-----BEGIN AGE ENCRYPTED FILE-----
…
-----END AGE ENCRYPTED FILE-----
```

Copy vào clipboard. Nếu dài hơn 4000 ký tự, hoặc không có clipboard: ghi thêm file `hostbook-<YYYYMMDD-HHMM>.hbshare` vào `~/Downloads` (không có thì thư mục hiện tại).

```
✓ Đã copy gói vào clipboard (2,1 KB)
╭────────────────────────────────────────────────────╮
│ Mật khẩu của gói:  cam-sao-bien-lua-73              │
│ Gửi mật khẩu này qua kênh KHÁC với kênh gửi gói.    │
╰────────────────────────────────────────────────────╯
> Copy mật khẩu (thay cho gói trong clipboard)
  Xong
```

Bảng từ (128 từ, không dấu, kiểm thử phải xác nhận đủ 128 và không trùng):

```
cam chanh dua xoai mit oi tao le nho man dao mo buoi na khe vai
meo cho ga vit bo trau ngua de heo tho cao soi gau ho bao voi
chim ca tom cua oc ech ran rua ong buom kien ve muoi nhen luon muc
nui song bien suoi rung dong lua gio mua nang may sao trang sam chop suong
ban ghe tu den quat chen bat dia thia noi chao binh ly coc am ro
xanh do tim vang nau hong xam bac lon be dai ngan nhanh cham nong lanh
di chay bay boi leo nhay hat doc viet nghe nhin cuoi an uong ngu choi
nha cong duong cau pho lang que truong lop san vuon ao ruong thuyen xe tau
```

### 6.2 Nhập gói

Lối vào:
- **Clipboard**: khi mở `hb`, nếu `clip_get | head -c 300` có `# hostbook-share v1` → hỏi "Có gói chia sẻ trong clipboard (12 server · từ Mac). Nhập?". Mỗi gói chỉ hỏi một lần: lưu sha256 của gói vừa hỏi vào `$STATE/last-offer`.
- `hb import <file>` và Công cụ → Nhập nhiều server: `import_flow` nhận ra dòng đầu `# hostbook-share` và chuyển sang luồng này.

Các bước:
1. Hỏi mật khẩu gói (hiện chữ, vì là mật khẩu dùng một lần đọc qua điện thoại), tối đa 3 lần. Giải mã → `tar -xzf` vào `tmpd`. Từ chối gói có đường dẫn chứa `..` hay bắt đầu bằng `/`, và file ngoài bố cục ở 6.1.
2. Dựng chỉ mục của gói: chạy `index_file` trên `conf/*.conf` của gói với một ssh config tạm chỉ `Include` thư mục đó.
3. Phân loại từng server của gói (so bộ giá trị: hostname, user, cổng, máy trung gian, tags, mô tả, loại và fingerprint của secret, các dòng tuỳ chọn khác):

   | Loại | Điều kiện | Đề xuất |
   |---|---|---|
   | MỚI | chưa có tên đó, không trùng địa chỉ | thêm |
   | GIỐNG | có tên đó, mọi giá trị bằng nhau | bỏ qua |
   | KHÁC | có tên đó, có giá trị khác | hỏi |
   | TRÙNG IP | chưa có tên đó nhưng có server khác cùng hostname và cổng | hỏi |

4. Secret của gói: key có fingerprint đã tồn tại → dùng lại secret sẵn có (ánh xạ tên gói → tên máy này). Tên đã tồn tại nhưng khác fingerprint → nhập với tên `<tên>-2` (tăng dần), ghi rõ trong màn xem trước. Mật khẩu trùng tên: giải mã bản của máy này để so; giống → dùng lại; khác → hỏi "Cập nhật / Giữ bản của tôi (nhập với tên <tên>-2)". Còn lại → mới.
5. Nhóm: chưa có → thêm (thành viên là các server tồn tại sau khi nhập); đã có → gộp thành viên.
6. Màn xem trước:

```
Gói từ Mac · 12 server · 2 nhóm · 2 secret                           ● 3 server prod

  + 7 server mới               vào dự án booking (mới), project-a
  = 3 server giống hệt         bỏ qua
  ≠ 2 server khác bản của bạn  ← cần chọn
      bk-api    HostName 10.8.1.4 → 10.8.1.40
      bk-db     thêm tag #mysql
  ! 1 server trùng IP          b-api2 trong gói trùng 10.2.0.5 với b-api của bạn

  Secret: key-booking (mới) · key-dev (đã có, giống hệt)

> Nhập theo đề xuất          thêm mới, bỏ qua giống hệt, hỏi từng server còn lại
  Cập nhật tất cả theo gói
  Giữ tất cả bản của tôi     chỉ thêm server mới
  Xem từng mục…
  Huỷ
```

   - Hỏi cho KHÁC: hiện các dòng khác nhau → "Cập nhật theo gói / Giữ bản của tôi / Giữ cả hai (bản trong gói thành `<tên>-2`) / Áp dụng lựa chọn này cho các server còn lại".
   - Hỏi cho TRÙNG IP: "Thêm như server mới / Bỏ qua / Thay thế `<tên đang có>`".
7. Áp dụng trong **một** `apply_change` (một commit: `Nhập gói từ Mac: +7 ~2`): kho secret chưa có → `vault_init`; ghi secret (mã hoá tới `vault.pub` của máy này, tên theo ánh xạ; sửa `# secret:` và đường dẫn `.pub` trong các khối theo ánh xạ); cập nhật = `delete_host_raw <tên> keep` rồi `write_host`; thêm = `write_host`; gộp nhóm. `validate` hỏng → trả lại nguyên trạng như các thao tác khác.
8. Ghi tên các server vừa thêm hoặc cập nhật vào `$STATE/last-import`. Màn kết thúc (**không tự kiểm tra kết nối**):

```
✓ Đã nhập: 7 mới · 2 cập nhật · 3 bỏ qua · 1 secret

Kiểm tra kết nối 9 server vừa nhập?
  Mỗi server thử đăng nhập rồi thoát ngay, không chạy lệnh nào, tối đa 5 giây.
> Kiểm tra
  Để sau
  Xem các server vừa nhập
  Hoàn tác lần nhập này
```

   "Xem các server vừa nhập": mở ô tìm ở chế độ lọc (`home` nhận file danh sách tên; `cmd_list` chỉ in các tên đó; đầu màn ghi "Đang xem 9 server vừa nhập · esc để xem tất cả"). "Hoàn tác" = `undo_last` (vì là một commit).

### 6.3 Kiểm tra kết nối (`check_hosts <tên>…`)

Lối vào: sau khi nhập; menu `→` của một server ("Kiểm tra kết nối"); menu `→` của nhiều server (6.5); `hb check [truy vấn…]`. Luôn do người dùng chủ động chọn. Hơn 30 server: câu hỏi ghi rõ số lượng.

1. Tách `explain()` thành hai phần: `classify_log <tên> <log>` → in `loại<TAB>một dòng lý do` (logic đang có), và phần vẽ khung. `check_hosts` dùng `classify_log`.
2. Chuẩn bị: `agent_ready`; nạp mọi key cần (một lần mở khoá).
3. Xếp tầng theo máy trung gian: tầng 0 = server không có máy trung gian, hoặc máy trung gian không nằm trong lượt kiểm tra; tầng k = server có máy trung gian ở tầng k−1. Máy trung gian thất bại → mọi server phụ thuộc ghi `· bỏ qua: máy trung gian <x> không vào được`, không thử.
4. Mỗi server, chạy song song từng lượt 8:
   - secret key hoặc không secret: `sshx -v -E <log> -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new <tên> true </dev/null`
   - secret mật khẩu (BatchMode sẽ tắt đăng nhập mật khẩu): bỏ `BatchMode`, đặt `SSH_ASKPASS_REQUIRE=force HB_ASKPASS_NOTTY=1` và `-o NumberOfPasswordPrompts=1`
   - chốt chặn: quá 8 giây thì dừng tiến trình đó, coi là hết thời gian.
5. Hiển thị trực tiếp: mỗi server một dòng (`⣾ đang mở kết nối…` → `✓ đăng nhập được bằng key-booking` / `✗ <lý do một dòng>`), vẽ lại tại chỗ mỗi 0,2 giây. Số server nhiều hơn số dòng màn hình: chỉ hiện bộ đếm `✓ 41 · ✗ 3 · còn 12`, xong thì liệt kê các server lỗi.
6. Lưu `$STATE/last-check.tsv` (tên, kết quả, lý do, đường dẫn log giữ trong `$STATE/tmp`). Menu sau cùng: "Xem chi tiết lỗi" (chọn server → khung lỗi đầy đủ từ log đã lưu) / "Thử lại các server lỗi" / "Xong".

### 6.4 Lệnh của M3

`hb share [truy vấn…]`, `hb import <file>` (mở rộng), `hb check [truy vấn…]`. Hook kiểm thử: `_share_build <thư mục ra> <kèm secret 0|1> <tên…>` (dựng thư mục gói, chưa mã hoá), `_share_pack <mật khẩu> <tên…>` (in gói), `_import_classify <thư mục gói>` (in bảng phân loại TSV), `_import_apply <thư mục gói> <theo-gói|giữ-của-tôi>`, `_check <tên…>` (in TSV kết quả, không vẽ).

### 6.5 Menu `→` khi đang chọn nhiều server

Hiện nay `→` chỉ tác động lên dòng đang trỏ. Khi có từ 2 dòng được chọn, `→` mở:

```
Với 5 server đã chọn
> Mở cùng lúc
  Kiểm tra kết nối
  Chia sẻ
  Lưu thành nhóm
```

`enter` (mở cùng lúc) và `^g` (lưu nhóm) giữ nguyên. Chân màn hình khi đang chọn: `enter mở N server · → thao tác khác · ^y chia sẻ · esc bỏ chọn`.

## 7. M4: giao diện và theme

### 7.1 File cấu hình

`${XDG_CONFIG_HOME:-~/.config}/hostbook/config`, dạng `khoá = giá trị`, dòng `#` là chú thích. Đọc bằng `cfg <khoá> <mặc định>`; ghi bằng `cfg_set` (giữ nguyên các dòng khác). Giá trị không hợp lệ: dùng mặc định và `doctor` báo đúng dòng.

| Khoá | Giá trị | Mặc định |
|---|---|---|
| `list` | `cards` · `compact` · `minimal` | `cards` |
| `border` | `rounded` · `sharp` · `bold` · `double` · `none` | `rounded` |
| `preview` | `right` · `bottom` · `hidden` | `right` |
| `separator` | `on` · `off` (đường kẻ giữa các thẻ) | `on` |
| `icons` | `unicode` · `ascii` · `nerd` | `unicode` |
| `theme` | tên theme có sẵn hoặc tên file trong `themes/` | `hostbook` |
| `key_ttl` | số giây, hoặc `8h`, `30m` | `8h` |
| `connect_timeout` | giây (biến môi trường `HOSTBOOK_CONNECT_TIMEOUT` vẫn thắng) | `15` |

### 7.2 Theme

Định dạng (theme có sẵn nằm trong script dạng hàm `theme_builtin <tên>` in ra đúng định dạng này; theme tự tạo là file `themes/<tên>.theme`):

```
accent  = 212     # màu nhấn: con trỏ, phím tắt, chữ khớp khi tìm
title   = 141     # tiêu đề, nhãn khung, tag
dim     = 244     # chữ phụ
ok      = 114
warn    = 221
err     = 204     # lỗi và prod
border  = 238
sel_bg  = 236     # nền dòng đang chọn
chip_fg = 235
chips   = 110 179 222 152 175 114 216 147    # nền chip dự án
base    = dark    # dark | light | 16   (nền mặc định của fzf)
```

Giá trị màu: số 0–255, hoặc `#rrggbb`, hoặc (chỉ theme kiểu terminal) tên ANSI `black red green yellow blue magenta cyan white` và `bright-…`. Kiểm tra bằng regex; sai thì báo `themes/x.theme dòng 4: "xanh" không phải màu hợp lệ` và dùng theme `hostbook`.

Theme có sẵn: `hostbook` (màu hiện tại), `dracula`, `nord`, `gruvbox`, `solarized-dark`, `solarized-light`, `light`, `high-contrast`, `terminal` (chỉ tên ANSI, `base = 16`, tự theo theme của terminal). Bảng màu cụ thể của từng theme lấy từ bảng màu chính thức của theme đó, ánh xạ vào 10 khoá trên; `light` và `solarized-light` có `base = light`, `dim` đủ đậm để đọc trên nền trắng.

`apply_theme` chạy lúc khởi động:
- đặt `CP CU CD CG CY CR` (và mới: `CBORDER`, `CSEL`) từ theme; hàm `esc_fg <màu>` sinh `38;5;N` hoặc `38;2;r;g;b` hoặc mã ANSI theo tên.
- `box` nhận tên ngữ nghĩa (`err warn ok title dim`) thay cho số; sửa mọi lời gọi `box 204|221|114|141|244`.
- xuất các biến `GUM_*_FOREGROUND` hiện có theo theme.
- `fzf_colors()` dựng chuỗi `--color`: `<base>,fg+:bold,hl:<accent>,hl+:<accent>,pointer:<accent>,marker:<accent>,prompt:<accent>,info:<dim>,border:<border>,label:<title>,ghost:<dim>,current-bg:<sel_bg>,header:-1`.
- tmux: `status-style bg=<ok>`, viền ô prod `fg=<err>`.
- Biến môi trường `NO_COLOR` có giá trị → ép theme `terminal` với `base = bw`.
- Chip dự án: màu = `chips[cksum(tên dự án) mod số chip]`. Server prod luôn có chữ `●prod` ngoài màu.

### 7.3 Ba kiểu danh sách

`cmd_list` tách thành `list_rows_compact` (logic hiện tại), `list_rows_cards`, `list_rows_minimal`. Cả ba in khoá ở trường 1 và phần đuôi ẩn để tìm; `cards` in mỗi mục kết thúc bằng NUL và fzf thêm `--read0`.

Thẻ (bề rộng `W = list_width`):

```
dòng 1:  <tên đậm, rộng namew>  <chip dự án>  <tag…, prod thành ●prod màu err>
dòng 2:  ␣␣<ip> · @<user> · <mô tả, cắt bằng …>            <lần dùng, căn phải>
```
- Nhóm: dòng 1 `▦ tên  nhóm · N server · kiểu mở`, dòng 2 liệt kê tên thành viên (cắt bằng `…`).
- Phiên tmux: dòng 1 `↺ tên  phiên tmux · N ô còn kết nối`, dòng 2 `enter để vào lại`.
- fzf thêm: `--highlight-line`, và khi `separator = on`: `--gap --gap-line=╌`.

Tối giản: một dòng, chỉ `tên` và `ip`, không chip; phần còn lại vào đuôi ẩn.

`fzf_opts()` dựng theo cấu hình:
- `border`: `rounded|sharp|bold|double` → `--style full:<kiểu>`; `none` → `--style minimal`. Khi có khung: `--input-label ' Tìm '`, `--list-label ' N server '`, `--preview-label ' Chi tiết '`; dòng trạng thái vẫn ở `--header`, phím tắt ở `--footer`.
- `preview`: `right` → `right,38%,wrap,<36(hidden)`; `bottom` → `down,40%,wrap`; `hidden` → `hidden`. Thêm `--bind 'ctrl-/:toggle-preview'`.
- `list_width` tính lại theo vị trí ô chi tiết và có trừ khung.
- Phím `^t` ở ô tìm: đổi qua lại `cards` ↔ `compact`, lưu vào config, vẽ lại (thoát vòng fzf bằng `--expect` rồi vào lại như các phím khác).

Biểu tượng: bảng `ICON_GROUP ICON_SESSION ICON_PROD ICON_OK ICON_ERR ICON_WARN ICON_PTR`. `unicode`: `▦ ↺ ● ✓ ✗ ! ▌`. `ascii`: `[g] [s] * ok x ! >`. `nerd`: glyph Nerd Font tương ứng (nf-fa-server, nf-fa-refresh, nf-fa-circle, nf-fa-check, nf-fa-times, nf-fa-warning). Mọi chỗ đang ghi cứng các ký tự này chuyển sang dùng bảng.

### 7.4 Màn Giao diện (Công cụ → Giao diện, hoặc `hb settings`)

```
‹ Giao diện
> Bộ có sẵn            Thẻ (mặc định)
  Kiểu danh sách       thẻ hai dòng
  Khung                bo tròn
  Ô chi tiết           bên phải
  Đường kẻ giữa thẻ    có
  Biểu tượng           unicode
  Theme màu            hostbook
  Tự tạo theme…
  Đặt lại mặc định
```

Chọn một mục → bộ chọn bằng fzf: bên trái là các giá trị, **bên phải xem trước trực tiếp** qua `--preview "'$SELF' _demo <khoá>={}"`. `_demo` dựng cấu hình tạm (cấu hình hiện tại + giá trị đang trỏ) rồi in một bản vẽ tĩnh của màn chính, rộng `$FZF_PREVIEW_COLUMNS`: khung vẽ bằng `gum style` theo kiểu viền, các dòng danh sách do **chính các hàm `list_rows_*`** sinh từ một chỉ mục mẫu cố định trong script (6 server, 1 nhóm, có prod, có mô tả dài), ô chi tiết mẫu, chân màn hình. Enter lưu, esc huỷ.

Bộ có sẵn (mỗi bộ đặt cả 6 khoá):

| Tên | list · border · preview · separator · icons · theme |
|---|---|
| Thẻ (mặc định) | cards · rounded · right · on · unicode · hostbook |
| Gọn | compact · rounded · right · off · unicode · hostbook |
| Tối giản | minimal · none · hidden · off · unicode · terminal |
| Dracula | cards · rounded · right · on · unicode · dracula |
| Nord gọn | compact · sharp · right · off · unicode · nord |
| Sáng | cards · rounded · right · on · unicode · light |
| Tương phản cao | compact · bold · right · off · ascii · high-contrast |

"Tự tạo theme…": hỏi tên → chép theme hiện tại (kèm chú thích từng khoá) vào `themes/<tên>.theme` → `$EDITOR` → kiểm tra (sai thì báo dòng, hỏi mở lại) → hiện `_demo` với theme đó → "Dùng theme này?" → `cfg_set theme <tên>`.

### 7.5 Bước chọn giao diện lúc cài lần đầu

Trong `first_run`, sau `setup`, trước "enter để bắt đầu": mở bộ chọn "Bộ có sẵn" (cùng màn ở 7.4), tiêu đề "Chọn giao diện · đổi lại bất cứ lúc nào trong Công cụ → Giao diện". Esc = giữ mặc định. Nếu biến `COLORFGBG` cho thấy nền sáng (phần sau dấu `;` cuối là `7` hoặc `15`), con trỏ đặt sẵn ở "Sáng". Người dùng cũ nâng cấp lên: không hỏi, giữ `compact` + `hostbook` (ghi vào config lần chạy đầu của bản mới) để không ai bị đổi giao diện bất ngờ; trợ giúp và ghi chú phát hành nhắc tới `^t` và Công cụ → Giao diện.

## 8. Kiểm thử

### 8.1 Khung kiểm thử

- Giữ `tests/test.sh`. Thêm biến cách ly, đặt ở đầu file: `HOSTBOOK_KEYCHAIN=file:$T/keychain`, `HOSTBOOK_MASTER=…`, `XDG_CONFIG_HOME=$T/config`, và **agent riêng**: `d=$(mktemp -d /tmp/hbt-XXXXXX); eval "$(ssh-agent -a $d/a.sock)"; [ "$SSH_AUTH_SOCK" = "$d/a.sock" ] || exit 1`, tắt agent trong `trap`. Không bài nào được chạm agent thật, Keychain thật hay `~/.config`.
- Bài cần server SSH thật để ở `tests/e2e.sh`, dùng container (Dockerfile như đã dùng khi làm v0.1: Ubuntu + openssh-server, một user có key, một user `tester` có mật khẩu, cổng `127.0.0.1:2222`). `tests/e2e.sh` tự bỏ qua khi không có Docker. CI: chạy `e2e.sh` trên job Ubuntu (có Docker); job macOS chỉ chạy `test.sh`.
- Màn hình: tiếp tục kiểm bằng tmux (`tmux new-session -d -x <rộng> -y <cao>`, `send-keys`, `capture-pane`) ở 60, 100, 200 cột cho mọi màn mới.

### 8.2 Bài kiểm thử theo mốc

**M1 (`test.sh`)**: tạo kho → có 3 file, `vault.pub` khớp danh tính; mật khẩu < 10 ký tự bị từ chối; sai mật khẩu chính thất bại, đúng thì mở; mã khôi phục mở được và sau `recover` mã cũ hết hiệu lực; đổi mật khẩu chính vẫn giải mã được secret cũ; thêm key: tạo `.age/.meta/.pub`, `.pub` quyền 600, giải mã ra đúng nội dung; thêm lại cùng key → trả tên cũ, không tạo file mới; key có passphrase → được gỡ; nội dung rác → từ chối; tên không hợp lệ → từ chối; `host_block` với secret key / mật khẩu / rỗng ra đúng các dòng ở 3.3; chỉ mục có trường 12, kể cả suy ra từ wildcard; `set_host_secret` đổi qua lại ba kiểu không để sót dòng cũ; đổi tên secret cập nhật mọi khối; xoá secret gỡ sạch dòng đăng nhập; chuyển key cũ: config được viết lại, `validate` đạt, file gốc còn nguyên; `git ls-files` không chứa file lạ trong `secrets/`; `load_secret` nạp vào agent riêng và `hb lock` gỡ ra; hai máy cùng sửa một secret → `conflict`, `_resolve theirs` lấy đủ ba file từ kho; `_askpass`: prompt mật khẩu lần đầu trả secret, lần hai với `NOTTY` trả rỗng; tra secret theo `user@máy` trong prompt.

**M1 (`e2e.sh`)**: vào server bằng key trong kho (agent chỉ có key đó qua `.pub`); vào bằng mật khẩu qua askpass; mật khẩu sai → khung lỗi đúng loại; đi qua máy trung gian mà hai chặng dùng hai secret khác nhau; khi chưa nạp key, ssh thường báo lỗi và `explain` nhận ra `locked`; câu hỏi host key với askpass (điểm chưa kiểm chứng ở mục 2); "đưa key mới lên server": key mới vào được, tuỳ chọn gỡ key cũ thì key cũ bị từ chối.

**M2**: các ca cố định cho `_suggest` trên chỉ mục mẫu: cùng /24 + cùng tiền tố → `auto 1`, đúng dự án, lý do có cả hai nhãn; hai dự án cùng /16 → `auto 0`, cả hai đều được liệt kê; tên miền `x.booking.vn` → nhãn cùng đuôi; `com.vn` không bị coi là tên miền gốc; danh sách trống → dự án `servers`, không có secret; máy trung gian chỉ được đề xuất khi ≥ 80%; tags theo ngưỡng 60% + từ trong tên; `peer` cao nhất ≥ 60 thì có dòng sao chép; giữ user/cổng người dùng đã dán.

**M3**: bảng từ đủ 128, không trùng; mật khẩu gói đúng dạng; `_share_build`: khối được làm phẳng (server thừa hưởng user từ wildcard vẫn có `User`), máy trung gian tự được kèm, nhóm lọc đúng, không kèm secret thì không còn dòng đăng nhập nào; gói mã hoá → giải mã bằng đúng mật khẩu, sai mật khẩu thất bại; gói có đường dẫn `../x` bị từ chối; `_import_classify` ra đúng 4 loại trên dữ liệu mẫu; secret trùng fingerprint được dùng lại, trùng tên khác fingerprint thành `-2`; `_import_apply theo-gói` và `giữ-của-tôi` cho kết quả đúng, là **một** commit, hoàn tác trả lại nguyên trạng; gói nhập vào máy chưa có kho secret → kho được tạo; `_check` (e2e): server vào được ✓, sai key ✗ đúng lý do, không tới được ✗ sau ≤ 8 giây, server phụ thuộc máy trung gian hỏng bị bỏ qua, tổng thời gian 8 server ≤ 10 giây.

**M4**: `cfg`/`cfg_set` đọc ghi đúng, giữ dòng khác; giá trị sai → mặc định + `doctor` báo dòng; mọi theme có sẵn qua được kiểm tra định dạng; theme sai báo đúng dòng; `NO_COLOR` → không có mã màu 256; ba kiểu danh sách: không dòng nào vượt `list_width` ở 60/100/200 cột, khoá ở trường 1, tìm `#tag`/`@user`/`secret:` vẫn ra; `cards` kết thúc mục bằng NUL; `_demo` chạy không lỗi cho mọi bộ có sẵn ở 40 và 80 cột; người dùng cũ không có config → `compact`.

### 8.3 Tiêu chí xong của mỗi mốc

Bộ kiểm thử đạt trên macOS và Ubuntu (CI xanh); các màn mới đã chụp qua tmux ở ba bề rộng và không vỡ; README và trợ giúp (`?`) đã cập nhật; `hb doctor` sạch trên máy này; đã commit và push; máy này `hb update` xong và các lệnh mới chạy được với dữ liệu thật.

## 9. Thứ tự thực thi

1. **Nền** (mục 3): age trong `check_deps` và `install.sh`; `.gitignore`/`.gitattributes`; trường 12 của chỉ mục; `tmpd`, `clip_get`; khung kiểm thử cách ly (8.1) và `tests/e2e.sh` với container.
2. **M1**: 4.1–4.2 kho và mở khoá → 4.4 thêm secret → 3.3 `host_block` mới + 4.7 `pick_login` trong thêm/sửa → 4.5 key qua agent trong `connect`/`open_many` → 4.6 askpass → 4.3 chuyển key cũ → 4.8 màn Secret → 4.9 xung đột → 4.10 doctor → "đưa key mới lên server" → README. Phát hành 0.2.0.
3. **M2**: `suggest` + kiểm thử → nối vào `add_host`, `pick_login`, `import_write`. Phát hành 0.2.1.
4. **M3**: `classify_log` tách khỏi `explain` → `check_hosts` → tạo gói → nhập gói → menu `→` nhiều server, `^y`, phát hiện clipboard. Phát hành 0.3.0.
5. **M4**: `cfg` + `apply_theme` (đổi mọi màu ghi cứng) → ba kiểu danh sách + `fzf_opts` → `_demo` → màn Giao diện → bước lúc cài lần đầu. Phát hành 0.4.0.

## 10. Ngoài phạm vi

Kho nhóm cho nhiều người; phân cấp nhiều tầng; giao diện tiếng Anh; Homebrew; viết lại bằng ngôn ngữ khác; xoay vòng danh tính của kho secret (đổi `vault.pub`); gói chia sẻ có hạn dùng.
