FROM alpine:latest

LABEL maintainer="trung.vuongthien@mail.mcgill.ca"

RUN apk add --update --no-cache \
	busybox-extras \
        curl \
        iptables \
	iptables-doc \
        ebtables \
        tcpdump \
	man  \
	man-pages \
	mdocml-apropos  \
	nmap \
	iproute2 \
	nftables \ 
	iperf \
	openssh \
	openssh-server \
	lynx

RUN apk add --update --no-cache --repository http://dl-cdn.alpinelinux.org/alpine/edge/testing \
	hping3 \
	hping3-doc \
	ettercap \
	ettercap-doc 

RUN mkdir /book_files && mkdir /book_files/iptables && mkdir /book_files/hping
COPY ruleset_1C.sh /book_files/iptables/ruleset_1C.sh
COPY ruleset_1S.sh /book_files/iptables/ruleset_1S.sh
COPY hping_ex.sh /book_files/hping/hping_ex.sh

CMD ["/bin/sh"]

