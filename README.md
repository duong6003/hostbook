# hostbook

Tìm, kết nối và đồng bộ danh sách server SSH ngay trong terminal. Không có tài khoản, không có dịch vụ trả phí: danh sách là file ssh config bình thường, đồng bộ giữa các máy của bạn bằng git qua mạng nội bộ.

*A terminal SSH host manager: fuzzy search, multi-server tmux sessions, and git-based sync between your own machines. UI is in Vietnamese.*

```
> #prod web                                                  4/15 │ a-web1  ● PROD
  hostbook  macbook · 15 server · ✓ đã đồng bộ với kho            │ Nginx chính, sau load balancer
  ▦ prod-web    nhóm · 4 server · chạy đồng loạt                  │ #prod #web #hcm
▌ a-web1        project-a  #prod #web #hcm   @ubuntu  10.0.1.11   │
  a-web2        project-a  #prod #web #hcm   @ubuntu  10.0.1.12   │ Host a-web1
  bk-api        booking    #prod #api        @deploy  10.8.1.4    │   HostName 10.0.1.11
                                                                  │   ProxyJump a-bastion
  enter ssh   → thao tác   tab chọn nhiều   ^a thêm   ^o công cụ   ? trợ giúp
```

## Cài đặt

```bash
curl -fsSL https://raw.githubusercontent.com/duong6003/hostbook/main/install.sh | bash
```

Chạy được trên macOS và Linux, không cần sudo, mọi thứ cài vào `~/.local`. Trình cài lấy fzf và gum bản phát hành chính thức (có kiểm tra checksum) khi máy chưa có hoặc bản có sẵn quá cũ. Chạy lại lệnh trên, hoặc `hostbook update`, để cập nhật.

Mở terminal mới rồi gõ `hostbook`, hoặc lối tắt `hb` (nếu máy đã có lệnh tên `hb` thì lối tắt là `hop`, trình cài sẽ báo). Lần đầu, app hỏi một câu để biết máy này là máy nào:

- **Dùng kho ở máy khác**: lấy danh sách server về máy này.
- **Đây là máy đầu tiên**: tạo danh sách mới, có thể nhập từ `~/.ssh/config` sẵn có.
- **Tạo kho trên máy này**: dành cho một máy luôn bật.

## Kho

Kho là nơi giữ bản gốc danh sách server: một git repo trên một máy luôn bật của bạn (máy ở nhà, NAS, VPS…), hoặc một repo git private bất kỳ (GitHub, Gitea…). Các máy chỉ cần thấy kho qua mạng: cùng Wi-Fi/LAN, VPN hay Tailscale đều được. App tự tìm các máy trong mạng LAN và Tailscale để bạn chọn.

Đồng bộ chạy nền mỗi lần mở app. Mỗi thay đổi được kiểm tra cú pháp (`ssh -G`) trước khi đẩy lên kho, nên file lỗi không bao giờ lan sang máy khác. Khi không kết nối được kho, thay đổi được giữ lại và tự đẩy khi kết nối lại.

Hai máy sửa cùng lúc được gộp theo từng server, không theo từng dòng: mỗi máy thêm một server, hay sửa hai server khác nhau, đều tự gộp. Chỉ khi cùng một server bị sửa khác nhau ở hai nơi, app mới mở màn **Gộp xung đột** để bạn chọn bản nào, hoặc sửa tay.

## Dùng hằng ngày

| Phím | Việc |
|---|---|
| gõ | tìm theo tên, `#tag`, `@user`, IP, mô tả. Hỗ trợ cú pháp fzf: `web prod` (VÀ), `db \| cache` (HOẶC), `!staging` (trừ), `^b-` (bắt đầu bằng) |
| `ctrl-t` | đổi nhanh giữa thẻ hai dòng và danh sách gọn |
| `ctrl-/` | ẩn/hiện ô chi tiết |
| `enter` | ssh vào server, mở nhóm, hoặc vào lại phiên tmux đang chạy |
| `→` | thao tác: chuyển tiếp cổng (gợi ý cổng theo tag, ví dụ `#postgres` → 5432), gửi/tải file, copy IP, mở trong tmux, đổi tên, chuyển dự án, xoá |
| `tab` | chọn nhiều server, `enter` để mở cùng lúc: chia ô, chạy đồng loạt, hoặc mỗi server một tab (cần tmux) |
| `^g` | lưu các server đã chọn thành nhóm |
| `^a` / `^e` | thêm / sửa server |
| `^o` | công cụ: nhập nhiều server, secret, kho, hoàn tác, kiểm tra máy |
| `?` | trợ giúp |

Thêm server chỉ cần dán địa chỉ (`10.0.1.13`, `ubuntu@10.0.1.13` hay `10.0.1.13:2222`) và đặt tên. Dự án, user, tags, máy trung gian và key được đoán từ các server cùng dải IP, và mỗi giá trị đoán đều ghi rõ đoán từ đâu.

Nhập nhiều server một lần (ví dụ khi chuyển từ Termius): `^o` → Nhập nhiều server, hoặc `hostbook import danh-sach.txt`. Mỗi dòng `tên user@ip[:cổng] #tag …`, hoặc dán nguyên một ssh config.

### Khi kết nối

Trong lúc kết nối, một dòng tiến trình cho biết đang ở bước nào và đã chờ bao lâu:

```
→ ssh a-web1 qua a-bastion
⣾ Đã vào a-bastion, đang nối tới 10.0.1.11:22 · 4s / tối đa 15s · ctrl-c để huỷ
```

Quá 15 giây (đổi bằng biến `HOSTBOOK_CONNECT_TIMEOUT`) thì dừng. Khi lỗi, app nói rõ hỏng ở bước nào, vì sao, và gợi ý việc nên làm:

```
╭──────────────────────────────────────────────────────────────╮
│ a-bastion không nối tới được 10.0.1.11:22 sau 15 giây         │
│ • Server đích đang tắt, hoặc sai IP                           │
│ • Hoặc tường lửa chặn từ a-bastion tới server                 │
│                                                               │
│ ✓ Vào máy trung gian a-bastion                                │
│ ✗ Từ a-bastion tới 10.0.1.11:22                               │
│ · Server SSH trả lời                                          │
│ · Đăng nhập (user ubuntu)                                     │
╰──────────────────────────────────────────────────────────────╯
```

Các trường hợp được nhận ra: không có phản hồi, từ chối cổng, sai tên máy, không có đường mạng, máy trung gian không vào được hoặc không đi tiếp được, server đóng kết nối, sai key hay sai mật khẩu, host key bị đổi. Có thể mở "Xem nhật ký kết nối" để xem chi tiết từ ssh. Mỗi ô tmux khi mở nhiều server cũng có dòng tiến trình và lời giải thích riêng.

Server có tag `prod` luôn hiện màu đỏ. Khi mở chế độ chạy đồng loạt trên server prod, app hỏi lại và mặc định là **Không**.

Từ dòng lệnh: `hb web1` (vào thẳng nếu đúng tên), `hostbook add ubuntu@10.0.1.13`, `hostbook import [file]`, `hostbook sync`, `hostbook doctor`, `hostbook update`.

Giao diện co giãn theo kích thước terminal: màn hẹp chỉ hiện cột quan trọng (tên, tag, IP), các cột còn lại vẫn tìm được; ô xem trước tự ẩn khi không đủ chỗ.

## Secret

Key SSH và mật khẩu đăng nhập server được giữ trong **kho secret**: đã mã hoá (bằng [age](https://github.com/FiloSottile/age)), đồng bộ qua kho cùng danh sách server, nên máy mới không phải chép key tay.

- Mỗi secret là một mục có tên (`key-booking`, `matkhau-deploy`) và dùng lại được cho nhiều server. Thay key một chỗ là mọi server dùng nó đều theo; app có thể đưa key mới lên các server giúp bạn.
- Khi thêm server, mục "Đăng nhập bằng" liệt kê các secret đã có (kèm gợi ý), hoặc dán key mới, nhập key từ file, tạo key mới, lưu mật khẩu.
- Kho secret mở bằng một **mật khẩu chính**. Mỗi máy chỉ nhập một lần, sau đó Keychain (macOS) hoặc secret-service (Linux) nhớ giúp. Lúc tạo kho, app hiện một **mã khôi phục** để dùng khi quên mật khẩu chính.
- Kết nối qua hostbook thì key được nạp vào ssh-agent khi cần (8 giờ), không ghi ra file. Để `ssh`, `scp`, VS Code Remote-SSH dùng được ngoài app: `hostbook unlock`. Gỡ ra: `hostbook lock`.
- Quản lý trong `^o` → Secret: thêm, thay, sửa, đổi tên, xoá, xem server nào đang dùng. Lệnh: `hostbook vault passwd`, `hostbook vault recover`.

## Giao diện (v0.4)

Chạy `hb settings`, hoặc `ctrl-o` → **Giao diện**. Mọi lựa chọn đều có xem trước; `enter` lưu, `esc` huỷ. Có ba kiểu danh sách (thẻ hai dòng, gọn một dòng, tối giản), năm kiểu khung, ô chi tiết bên phải/bên dưới/ẩn, đường kẻ và biểu tượng Unicode/ASCII/Nerd Font. Biểu tượng Nerd Font cần font hỗ trợ. Server production luôn có nhãn `prod`, kể cả khi tắt màu.

Bảy bộ có sẵn: Thẻ, Gọn, Tối giản, Dracula, Nord gọn, Sáng, Tương phản cao. Có thêm các theme Gruvbox, Solarized tối/sáng và theme theo màu terminal. `NO_COLOR=1 hb` bỏ màu. Lần cài mới có bước chọn giao diện; khi nâng cấp, app giữ kiểu gọn để bạn tự chọn lúc thuận tiện.

Cấu hình riêng từng máy, **không đồng bộ**, ở `${XDG_CONFIG_HOME:-~/.config}/hostbook/config`:

```ini
list = cards              # cards, compact, minimal
border = rounded          # rounded, sharp, bold, double, none
preview = right           # right, bottom, hidden
separator = on            # on, off
icons = unicode           # unicode, ascii, nerd
theme = hostbook
key_ttl = 8h               # thời gian giữ key trong agent; cũng nhận phút/giây
connect_timeout = 15      # giây; HOSTBOOK_CONNECT_TIMEOUT được ưu tiên
```

**Tự tạo theme…** chép theme hiện tại vào `themes/<tên>.theme`, mở bằng `$EDITOR`, kiểm tra và cho xem trước trước khi dùng. Màu nhận số `0–255`, `#rrggbb`, hoặc tên ANSI (`red`, `bright-cyan`…). Giá trị cấu hình/theme sai sẽ dùng mặc định; `hb doctor` chỉ rõ dòng cần sửa. Nội dung file cấu hình được đọc như dữ liệu, không chạy như shell script.

## Dữ liệu

```
~/ssh-hosts/                 đồng bộ với kho
  project-a.conf             ssh config chuẩn, mỗi dự án một file
  groups.tsv                 các nhóm
  secrets/                   kho secret (đã mã hoá): <tên>.age, .meta, .pub
  .gitattributes             bật bộ gộp theo từng server
~/.config/hostbook/          cấu hình giao diện và theme riêng từng máy
~/.local/state/hostbook/     riêng từng máy: lịch sử dùng, chỉ mục
~/.ssh/config                có thêm dòng: Include ~/ssh-hosts/*.conf
```

Mỗi server là một khối `Host` bình thường, thêm hai dòng comment tuỳ chọn:

```
# tags: prod web hcm
# desc: Nginx chính
Host a-web1
  HostName 10.0.1.11
  User ubuntu
```

Vì là ssh config chuẩn, `ssh a-web1`, VS Code Remote-SSH, scp… đều dùng được, kể cả khi không mở hostbook. Bỏ hostbook đi cũng không mất gì.

## Bảo mật

- Key và mật khẩu chỉ rời máy ở dạng đã mã hoá. Ai lấy được kho vẫn cần mật khẩu chính (hoặc mã khôi phục) mới đọc được secret, nên mật khẩu chính phải đủ mạnh (tối thiểu 10 ký tự).
- Trên mỗi máy, danh tính của kho secret nằm trong Keychain của hệ điều hành: ai dùng được phiên đăng nhập của bạn trên máy đó thì dùng được secret. `hostbook lock --forget` để máy hỏi lại mật khẩu chính.
- Danh sách server không đi qua dịch vụ bên ngoài nào, chỉ giữa các máy của bạn.
- `~/ssh-hosts` và thư mục trạng thái chỉ chủ máy đọc được.
- Tên server chứa ký tự lạ bị bỏ qua (`hostbook doctor` sẽ liệt kê), để dữ liệu từ kho không thể chạy lệnh trên máy bạn.

## Gỡ cài đặt

```bash
rm -rf ~/.local/share/hostbook ~/.local/state/hostbook ~/.local/bin/hostbook
```

Rồi xoá dòng `alias hb=hostbook` trong `~/.zshrc` hoặc `~/.bashrc`. Danh sách server trong `~/ssh-hosts` vẫn còn và vẫn dùng được với `ssh`.

## Phát triển

```bash
bash tests/test.sh
bash tests/ui.sh
bash tests/tui.sh  # cần tmux; tự bỏ qua nếu chưa có
bash tests/e2e.sh  # cần Docker; tự bỏ qua nếu chưa có
```

Bộ kiểm thử chạy trên dữ liệu tạm, không đụng `~/ssh-hosts`. CI chạy trên macOS và Ubuntu.

## Giấy phép

MIT
