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

Mở terminal mới rồi gõ `hostbook` (hoặc `s`). Lần đầu, app hỏi một câu để biết máy này là máy nào:

- **Dùng kho ở máy khác**: lấy danh sách server về máy này.
- **Đây là máy đầu tiên**: tạo danh sách mới, có thể nhập từ `~/.ssh/config` sẵn có.
- **Tạo kho trên máy này**: dành cho một máy luôn bật.

## Kho

Kho là nơi giữ bản gốc danh sách server: một git repo trên một máy luôn bật của bạn (máy ở nhà, NAS, VPS…). Các máy chỉ cần thấy máy chứa kho qua mạng: cùng Wi-Fi/LAN, VPN hay Tailscale đều được. App tự tìm các máy trong mạng LAN và Tailscale để bạn chọn.

Đồng bộ chạy nền mỗi lần mở app. Mỗi thay đổi được kiểm tra cú pháp (`ssh -G`) trước khi đẩy lên kho, nên file lỗi không bao giờ lan sang máy khác. Khi không kết nối được kho, thay đổi được giữ lại và tự đẩy khi kết nối lại.

## Dùng hằng ngày

| Phím | Việc |
|---|---|
| gõ | tìm theo tên, `#tag`, `@user`, IP, mô tả. Hỗ trợ cú pháp fzf: `web prod` (VÀ), `db \| cache` (HOẶC), `!staging` (trừ), `^b-` (bắt đầu bằng) |
| `enter` | ssh vào server, mở nhóm, hoặc vào lại phiên tmux đang chạy |
| `→` | thao tác: chuyển tiếp cổng (gợi ý cổng theo tag, ví dụ `#postgres` → 5432), copy IP, mở tab mới, sửa |
| `tab` | chọn nhiều server, `enter` để mở cùng lúc: chia ô, chạy đồng loạt, hoặc mỗi server một tab (cần tmux) |
| `^g` | lưu các server đã chọn thành nhóm |
| `^a` / `^e` | thêm / sửa server |
| `^o` | công cụ: kho, sao chép key, hoàn tác, kiểm tra máy |
| `?` | trợ giúp |

Thêm server chỉ cần dán địa chỉ (`10.0.1.13`, `ubuntu@10.0.1.13` hay `10.0.1.13:2222`) và đặt tên. Dự án, user, tags, máy trung gian và key được đoán từ các server cùng dải IP, và mỗi giá trị đoán đều ghi rõ đoán từ đâu.

Server có tag `prod` luôn hiện màu đỏ. Khi mở chế độ chạy đồng loạt trên server prod, app hỏi lại và mặc định là **Không**.

Từ dòng lệnh: `hostbook web1` (vào thẳng nếu đúng tên), `hostbook add ubuntu@10.0.1.13`, `hostbook sync`, `hostbook doctor`.

## Dữ liệu

```
~/ssh-hosts/                 đồng bộ với kho
  project-a.conf             ssh config chuẩn, mỗi dự án một file
  groups.tsv                 các nhóm
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

- Private key không bao giờ được đồng bộ. Mỗi máy nên có key riêng; công cụ "Sao chép key" chỉ dùng khi chuyển máy.
- Danh sách server không đi qua dịch vụ bên ngoài nào, chỉ giữa các máy của bạn.
- `~/ssh-hosts` và thư mục trạng thái chỉ chủ máy đọc được.
- Tên server chứa ký tự lạ bị bỏ qua (`hostbook doctor` sẽ liệt kê), để dữ liệu từ kho không thể chạy lệnh trên máy bạn.

## Gỡ cài đặt

```bash
rm -rf ~/.local/share/hostbook ~/.local/state/hostbook ~/.local/bin/hostbook
```

Rồi xoá dòng `alias s=hostbook` trong `~/.zshrc` hoặc `~/.bashrc`. Danh sách server trong `~/ssh-hosts` vẫn còn và vẫn dùng được với `ssh`.

## Phát triển

```bash
bash tests/test.sh
```

Bộ kiểm thử chạy trên dữ liệu tạm, không đụng `~/ssh-hosts`. CI chạy trên macOS và Ubuntu.

## Giấy phép

MIT
