# hostbook v0.2: ghi chú thảo luận

Tài liệu này chỉ ghi lại các ý đã bàn và đã chốt về hướng đi. Kế hoạch chi tiết cho từng phần sẽ được phân tích và chốt ở phiên sau.

## 1. Kho secret (key và mật khẩu)

- Secret (key SSH, mật khẩu) được quản lý ngay trong app, trong một **kho secret**.
- Kho secret luôn được mã hoá, không bao giờ nằm dạng chữ thường trên đĩa, và mở bằng một **mật khẩu chính**.
- Kho secret đồng bộ qua kho, giống như danh sách server.
- Mỗi secret là **một mục có tên** (ví dụ `key-booking`, `matkhau-deploy`), dùng lại được cho nhiều server, và biết đang được những server nào dùng.
- **Không hỗ trợ trỏ tới file key có sẵn trên máy**, vì như vậy không chia sẻ được. Key luôn được đưa vào kho secret.
- Key được nhận diện bằng dấu vân tay (fingerprint) để chống trùng: dán lại hoặc nhập lại cùng một key thì dùng lại mục đã có.
- Có cách để `ssh`, VS Code, scp ngoài app vẫn dùng được key trong kho secret (mở khoá, nạp vào ssh-agent trong một khoảng thời gian).

## 2. Đăng nhập khi thêm hoặc sửa server

- Hỗ trợ đăng nhập bằng **key** hoặc bằng **mật khẩu**.
- Mục "Đăng nhập bằng" có:
  - danh sách secret đã lưu, **được gợi ý sẵn** dựa trên các server liên quan, có ghi lý do;
  - dán key mới;
  - nhập key từ đường dẫn (app đọc nội dung vào kho secret, không trỏ tới file);
  - mật khẩu mới.

## 3. Chỉnh sửa secret

- Có chỗ quản lý secret, cho phép sửa linh hoạt: thay key (dán hoặc nhập từ file), tạo key mới, sửa nội dung, đổi mật khẩu, copy public key, đổi tên, xem server nào đang dùng, xoá (báo trước server bị ảnh hưởng).

## 4. Gợi ý thông minh khi thêm server

- Gợi ý phải **có căn cứ và cho thấy căn cứ**. Cách hiện tại (chỉ nhìn dải IP rồi lấy theo số đông) chưa hợp lý.
- Hướng đã chốt:
  - chấm điểm theo nhiều dấu hiệu: cùng dải IP, tên cùng tiền tố, cùng đuôi tên miền, từ trong tên trùng tag, dự án dùng gần đây…
  - hiện vài lựa chọn hàng đầu kèm lý do;
  - gợi ý "giống server nào nhất" để sao chép thiết lập;
  - không đủ chắc thì hỏi, không tự đoán.

## 5. Chia sẻ

- **Phạm vi chia sẻ = những gì đang thấy trong ô tìm** (hoặc các dòng đã chọn). Tái sử dụng tìm kiếm; chất lượng chia sẻ phụ thuộc chất lượng dữ liệu nhập vào.
- Về bản chất, chia sẻ giống đồng bộ nhưng giới hạn theo bộ lọc: chuyển hết server, tag, mô tả, nhóm liên quan, và **mặc định kèm secret**.
- **Không cần kết nối giữa hai máy.** Gói chia sẻ là một đoạn chữ hoặc file đã mã hoá, gửi qua kênh bất kỳ (chat, email); mật khẩu của gói gửi qua kênh khác.

## 6. Nhập gói chia sẻ

- Có **giao diện nhập**: xem trước rồi xác nhận.
- So trùng theo từng server: đã có và giống hệt thì bỏ qua; khác thì cho chọn cập nhật, giữ bản của mình, hoặc giữ cả hai; trùng IP thì nhắc. Secret so trùng theo dấu vân tay.
- Sau khi nhập có **màn kiểm tra**: hiện tổng kết, rồi **hỏi** người dùng có muốn kiểm tra kết nối các server vừa nhập không. **Không tự chạy.**
- Tính năng kiểm tra kết nối cũng dùng được độc lập, cho các server bất kỳ.

## 7. Giao diện và theme

- Đi theo hướng tận dụng các tuỳ chọn trình bày của fzf (không viết lại giao diện bằng ngôn ngữ khác).
- Hỗ trợ **nhiều kiểu hiển thị** (ví dụ thẻ hai dòng, gọn một dòng, tối giản), các kiểu khung, vị trí ô chi tiết, và **theme màu** (gồm theme sáng, tương phản cao, theo terminal).
- Cho phép người dùng **tự tuỳ chỉnh** (file cấu hình, tự tạo theme).
- Chọn ở **Cài đặt** của app, và ở **bước cài lần đầu**, **có xem trước** khi chọn.

## Để sau, chưa làm trong v0.2

- Kho nhóm, để nhiều người cùng theo dõi một danh sách được cập nhật liên tục.
- Phân cấp nhiều tầng: đã bàn và không làm, vì dự án, tag và nhóm kết hợp với ô tìm là đủ.
- Giao diện tiếng Anh.
- Cài qua Homebrew.
- Kiểm tra việc tìm máy qua mDNS trong mạng LAN thật.

## Phiên sau

Phân tích và chốt kế hoạch chi tiết cho từng phần ở trên: thứ tự làm, cách làm, giao diện cụ thể, và tiêu chí hoàn thành.

Kế hoạch chi tiết để thực thi: [implement-v0.2.md](implement-v0.2.md).
