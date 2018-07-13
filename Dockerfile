FROM alpine:3.7
  
RUN apk update && \
    apk add --no-cache python3 linux-headers \
    gcc python3-dev musl-dev && \
    python3 -m ensurepip && \
    rm -r /usr/lib/python*/ensurepip && \
    pip3 install --upgrade pip setuptools && \
    if [ ! -e /usr/bin/pip ]; then ln -s pip3 /usr/bin/pip ; fi && \
    if [[ ! -e /usr/bin/python ]]; then ln -sf /usr/bin/python3 /usr/bin/python; fi && \
    rm -r /root/.cache

COPY ./requirements.txt /tmp

RUN pip install -r /tmp/requirements.txt

WORKDIR /opt/app-root/src
COPY . .

USER root

CMD ["python3", "server.py"]
