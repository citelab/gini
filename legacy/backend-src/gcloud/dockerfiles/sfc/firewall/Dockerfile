FROM citelab/alpine:latest

LABEL maintainer="trung.vuongthien@mail.mcgill.ca"

COPY ./entry_script.sh /bin/entry_script.sh
RUN chmod 755 /bin/entry_script.sh

ENTRYPOINT /bin/entry_script.sh

