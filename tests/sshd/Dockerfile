# Server SSH dùng cho tests/e2e.sh: user root đăng nhập bằng key (e2e.sh tự cài key), user tester đăng nhập bằng mật khẩu.
FROM ubuntu:24.04
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server >/dev/null \
 && mkdir -p /run/sshd /root/.ssh && chmod 700 /root/.ssh \
 && useradd -m -s /bin/bash tester && echo 'tester:matkhau123' | chpasswd \
 && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/; s/^#\?MaxAuthTries.*/MaxAuthTries 6/' /etc/ssh/sshd_config
CMD ["/usr/sbin/sshd","-D"]
