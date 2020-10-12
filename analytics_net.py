import logging
import tempfile
from kubernetes import client, config
import urllib3
import base64
import requests

urllib3.disable_warnings()
logger = logging.getLogger(__name__)


class AnalyticsNet(object):
    namespace = ''
    token = None
    token_expires = 0
    max_frequency = 600
    data = {}
    data_time = 0
    use_kubeconfig = False
    hostname = None
    trawler = None
    certificates = None
    status_map = {"green": 2, "yellow": 1, "red": 0}

    def __init__(self, config, trawler):
        # Takes in config object and trawler instance it's behind
        # In k8s or outside
        self.use_kubeconfig = trawler.use_kubeconfig
        # Namespace to find managemnet pods
        self.namespace = config.get('namespace', 'default')
        # Maximum frequency to pull data from APIC
        self.max_frequency = int(config.get('frequency', 600))
        self.trawler = trawler
        if self.use_kubeconfig:
            logger.error("Analytics metrics currently only available in cluster setting localhost:9200 for testing")
            self.hostname = 'localhost:9200'
            self.find_hostname_and_certs()
        else:
            self.find_hostname_and_certs()

    def find_hostname_and_certs(self):
        try:
            # Initialise the k8s API
            if self.use_kubeconfig:
                config.load_kube_config()
                v1 = client.CoreV1Api()
            else:
                config.load_incluster_config()
                logger.info("In cluster, so looking for analytics-storage service")
                v1 = client.CoreV1Api()
                # Identify analytics-storage service
                servicelist = v1.list_namespaced_service(namespace=self.namespace)
                logger.info("found {} services in namespace {}".format(len(servicelist.items), self.namespace))
                for service in servicelist.items:
                    if 'analytics-storage' in service.metadata.name:
                        for port_object in service.spec.ports:
                            if port_object.name == 'http-es':
                                port = 9200  # default
                                if port_object.port:
                                    port = port_object.port
                                self.hostname = "{}.{}.svc:{}".format(service.metadata.name, self.namespace, port)
                if self.hostname:
                    logger.info("Identified service host: {}".format(self.hostname))
            # Get certificates to communicate with analytics
            secrets_response = v1.list_namespaced_secret(namespace=self.namespace)
            cert = None
            for item in secrets_response.items:
                if item.metadata.name.startswith('analytics-storage-velox-certs'):
                    cert = base64.b64decode(item.data['analytics-storage_client_public.cert.pem'])
                    key = base64.b64decode(item.data['analytics-storage_client_private.key.pem'])
                    break
                elif item.metadata.name == 'analytics-client':
                    cert = base64.b64decode(item.data['tls.crt'])
                    key = base64.b64decode(item.data['tls.key'])
                    break
            if cert:
                combined = key + "\n".encode() + cert
                self.certificates = tempfile.NamedTemporaryFile('w', delete=False)
                with self.certificates as certfile:
                    certfile.write(combined.decode())
        except client.rest.ApiException as e:
            logger.error('Error calling kubernetes API')
            logger.exception(e)

    def fish(self):
        if self.hostname:
            r = requests.get('https://{}/_cluster/health'.format(self.hostname), verify=False,
                             cert=self.certificates.name)

            health_obj = r.json()
            logger.debug(r.text)
            try:
              cluster_status = self.status_map[health_obj['status']]
            except KeyError:
              cluster_status = -1

            self.trawler.set_gauge('analytics', 'cluster_status', cluster_status)
            self.trawler.set_gauge('analytics', 'data_nodes_total', health_obj['number_of_data_nodes'])
            self.trawler.set_gauge('analytics', 'nodes_total', health_obj['number_of_nodes'])
            self.trawler.set_gauge('analytics', 'active_primary_shards_total', health_obj['active_primary_shards'])
            self.trawler.set_gauge('analytics', 'active_shards_total', health_obj['active_shards'])
            self.trawler.set_gauge('analytics', 'relocating_shards_total', health_obj['relocating_shards'])
            self.trawler.set_gauge('analytics', 'initializing_shards_total', health_obj['initializing_shards'])
            self.trawler.set_gauge('analytics', 'unassigned_shards_total', health_obj['unassigned_shards'])
            self.trawler.set_gauge('analytics', 'initializing_shards_total', health_obj['initializing_shards'])
            self.trawler.set_gauge('analytics', 'pending_tasks_total', health_obj['number_of_pending_tasks'])


if __name__ == "__main__":
    net = AnalyticsNet({"namespace": "apic-management"}, None)
    net.fish()
