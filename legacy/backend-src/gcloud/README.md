# dynamic-proxy
Dynamic configuration of HAProxy inside Docker containers using consul-template and registrator

## Quick start:

For an example config file, look at `examples/sample_config.json`

To start a docker cloud from the config file, run:
```shell
python main.py -f path/to/config/file.json
```

## Example

```shell
python main.py -f ./examples/sample_config.json
```

Wait for Docker to start all the containers (you can check `docker ps`), there will be 13 new containers created. Note that the assigned address for proxy container is 192.168.10.128. And the load balancing algorithm is round robin

Send HTTP requests to the first service, which is running on port 80:

```shell
for i in $(seq 10); do curl 192.168.10.128:80; done
```

Now send HTTP requests to the second service, which is listening on port 8080:
```shell
for i in $(seq 10); do curl 192.168.10.128:8080; done
```

You will see different hostnames and IP addresses in the replies. Which proves that there are more than one server to handle web requests.
