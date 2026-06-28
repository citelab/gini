"""Real Kubernetes: roles, manifest generation, the k3s container, apply/read-back."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _role, _svc
from gini.services.orchestrator import _compose


def _k8s_topo():
    t = Topology("k8s")
    cl = t.add_device("k8s_cluster")
    pod = t.add_device("pod")
    pod.properties.update({"Image": "nginxdemos/hello:latest", "Replicas": "3", "Port": "80"})
    asg = t.add_device("instance_group")
    asg.properties.update({"Min": "2", "Max": "8", "TargetCPU": "50"})
    t.add_link(cl.id, pod.id); t.add_link(pod.id, asg.id)
    return t, cl, pod, asg


def test_k8s_roles_no_longer_groups():
    assert _role("k8s_cluster") == "k8scluster"
    assert _role("pod") == "k8sworkload"
    assert _role("instance_group") == "hpa"
    assert _role("k8s_node") == "k8snode"           # was mis-compiling to "machine"


def test_cluster_builds_deployment_service_and_hpa():
    t, cl, pod, asg = _k8s_topo()
    cfg = RuntimeCompiler().compile(t)
    assert len(cfg.k8s) == 1
    k = cfg.k8s[0]
    assert k.svc == _svc(cl.name) and "rancher/k3s" in k.image
    d = k.deployments[0]
    assert d["replicas"] == 3 and d["port"] == 80
    assert d["hpa"] == {"min": 2, "max": 8, "cpu": 50}
    m = k.manifests
    assert "kind: Deployment" in m and "kind: Service" in m
    assert "kind: HorizontalPodAutoscaler" in m
    assert "averageUtilization: 50" in m and "replicas: 3" in m
    assert "cpu: 50m" in m                           # resource requests (HPA needs them)


def test_pod_without_hpa_has_no_autoscaler():
    t = Topology("k8s")
    cl = t.add_device("k8s_cluster"); pod = t.add_device("pod")
    t.add_link(cl.id, pod.id)
    k = RuntimeCompiler().compile(t).k8s[0]
    assert k.deployments[0]["hpa"] is None
    assert "HorizontalPodAutoscaler" not in k.manifests


def test_compose_emits_k3s_container():
    t, *_ = _k8s_topo()
    compose = _compose(RuntimeCompiler().compile(t))
    assert "rancher/k3s" in compose
    assert "privileged: true" in compose
    assert "/gini-manifests:ro" in compose
    assert "command: server --disable=traefik --snapshotter=native" in compose


def test_k8s_links_are_intent_not_data_segments():
    t, *_ = _k8s_topo()
    cfg = RuntimeCompiler().compile(t)
    assert cfg.subnets == {}                          # no data subnet from k8s links
    assert any("k8s link" in n for n in cfg.notes)


def test_orchestrator_apply_and_pods_safe_when_not_running():
    from gini.services.orchestrator import Orchestrator
    import gini.runtime as rt
    o = Orchestrator(rt.__path__[0])
    ok, msg = o.k8s_apply("k8s1")
    assert ok is False and "not running" in msg
    assert o.k8s_pods("k8s1") == []


def test_orchestrator_reads_pods(monkeypatch):
    from gini.services.orchestrator import Orchestrator
    import gini.runtime as rt
    import subprocess as sp, json

    class R:
        def __init__(s, out, rc=0): s.stdout, s.returncode, s.stderr = out, rc, ""

    payload = json.dumps({"items": [
        {"metadata": {"name": "pod1-abc", "namespace": "default", "labels": {"app": "pod1"}},
         "status": {"phase": "Running"}, "spec": {"nodeName": "k3s"}},
        {"metadata": {"name": "coredns-x", "namespace": "kube-system"},
         "status": {"phase": "Running"}}]})
    monkeypatch.setattr(sp, "run", lambda cmd, **k: R(payload))
    o = Orchestrator(rt.__path__[0]); o.workdir = "/tmp/x"
    pods = o.k8s_pods("k8s1")
    assert len(pods) == 1 and pods[0]["app"] == "pod1"   # kube-system hidden
    assert pods[0]["phase"] == "Running"


def test_k8s_metrics_parses_hpa_and_deployment(monkeypatch):
    """The Live-view feed: replicas + CPU% vs HPA target, per deployment."""
    from gini.services.orchestrator import Orchestrator
    import gini.runtime as rt
    import subprocess as sp, json

    class R:
        def __init__(s, out, rc=0): s.stdout, s.returncode, s.stderr = out, rc, ""

    payload = json.dumps({"items": [
        {"kind": "Deployment",
         "metadata": {"name": "pod1", "namespace": "default"},
         "spec": {"replicas": 3}, "status": {"readyReplicas": 3}},
        {"kind": "HorizontalPodAutoscaler",
         "metadata": {"name": "pod1", "namespace": "default"},
         "spec": {"minReplicas": 2, "maxReplicas": 8,
                  "scaleTargetRef": {"name": "pod1"},
                  "metrics": [{"resource": {"name": "cpu",
                               "target": {"averageUtilization": 50}}}]},
         "status": {"currentReplicas": 3,
                    "currentMetrics": [{"resource": {"name": "cpu",
                                        "current": {"averageUtilization": 12}}}]}},
        {"kind": "Deployment", "metadata": {"name": "coredns",
         "namespace": "kube-system"}, "spec": {"replicas": 1}}]})
    monkeypatch.setattr(sp, "run", lambda cmd, **k: R(payload))
    o = Orchestrator(rt.__path__[0]); o.workdir = "/tmp/x"
    m = o.k8s_metrics("k8s1")
    d = m["deployments"]["pod1"]
    assert "coredns" not in m["deployments"]              # kube-system hidden
    assert d["replicas"] == 3 and d["desired"] == 3
    assert d["min"] == 2 and d["max"] == 8
    assert d["target_pct"] == 50 and d["cpu_pct"] == 12
    assert m["pods"] == 3


def test_k8s_metrics_v1_hpa_fallback(monkeypatch):
    """autoscaling/v1 HPAs expose CPU on different fields — still parsed."""
    from gini.services.orchestrator import Orchestrator
    import gini.runtime as rt
    import subprocess as sp, json

    class R:
        def __init__(s, out, rc=0): s.stdout, s.returncode, s.stderr = out, rc, ""

    payload = json.dumps({"items": [
        {"kind": "HorizontalPodAutoscaler",
         "metadata": {"name": "pod1", "namespace": "default"},
         "spec": {"minReplicas": 1, "maxReplicas": 5,
                  "scaleTargetRef": {"name": "pod1"},
                  "targetCPUUtilizationPercentage": 70},
         "status": {"currentReplicas": 2, "currentCPUUtilizationPercentage": 41}}]})
    monkeypatch.setattr(sp, "run", lambda cmd, **k: R(payload))
    o = Orchestrator(rt.__path__[0]); o.workdir = "/tmp/x"
    d = o.k8s_metrics("k8s1")["deployments"]["pod1"]
    assert d["target_pct"] == 70 and d["cpu_pct"] == 41 and d["replicas"] == 2


def test_k8s_scale_and_patch_build_kubectl(monkeypatch):
    """The Replicas / Target-CPU sliders issue the right kubectl verbs."""
    from gini.services.orchestrator import Orchestrator
    import gini.runtime as rt
    import subprocess as sp, json

    calls = []

    class R:
        stdout = ""; returncode = 0; stderr = ""

    def fake_run(cmd, **k):
        calls.append(cmd); return R()
    monkeypatch.setattr(sp, "run", fake_run)
    o = Orchestrator(rt.__path__[0]); o.workdir = "/tmp/x"

    ok, _ = o.k8s_scale("k8s1", "pod1", "5")
    assert ok and calls[-1][-3:] == ["scale", "deployment/pod1", "--replicas=5"]

    ok, _ = o.k8s_set_hpa("k8s1", "pod1", target=80, mn=2, mx=9)
    assert ok and "patch" in calls[-1] and "hpa/pod1" in calls[-1]
    spec = json.loads(calls[-1][-1])["spec"]
    assert spec["minReplicas"] == 2 and spec["maxReplicas"] == 9
    assert spec["metrics"][0]["resource"]["target"]["averageUtilization"] == 80


def test_k8s_methods_safe_when_not_running():
    from gini.services.orchestrator import Orchestrator
    import gini.runtime as rt
    o = Orchestrator(rt.__path__[0])
    assert o.k8s_metrics("k8s1") == {}
    assert o.k8s_scale("k8s1", "pod1", 3)[0] is False
    assert o.k8s_set_hpa("k8s1", "pod1", target=50)[0] is False


def test_inspector_k8s_live_view_uses_kubernetes_layout():
    """Selecting a running Pod shows the K8s layout, fed by the kubectl snapshot; an
    Autoscaling Group reads the same deployment, and both keep continuous history."""
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    from gini.ui.live_metrics import K8S_LAYOUT, CLOUD_LAYOUT
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    cl = w.api.add_device("k8s_cluster", x=60, y=60)["id"]
    pod = w.api.add_device("pod", x=240, y=60)["id"]
    asg = w.api.add_device("instance_group", x=420, y=60)["id"]
    w.ctx.add_link(cl, pod); w.ctx.add_link(pod, asg)
    app.processEvents()
    dep = _svc(w.ctx.topology.devices[pod].name)

    insp = w.inspector
    insp.set_live_running(True)
    insp.tabs.setCurrentWidget(insp._live_host)

    snap = {"deployments": {dep: {"replicas": 3, "desired": 3, "min": 2, "max": 8,
                                  "target_pct": 50, "cpu_pct": 12}}, "pods": 3}
    for _ in range(3):
        insp.set_k8s_snapshot(snap)

    # Pod selected -> kubernetes layout, pointed at the deployment's history buffer
    w.ctx.bus.selection_changed.emit(pod); app.processEvents()
    assert insp.metrics._layout is K8S_LAYOUT
    assert insp.metrics._d is insp._hist[dep]
    assert list(insp._hist[dep]["replicas"]) == [3.0, 3.0, 3.0]
    assert list(insp._hist[dep]["cpu_pct"]) == [12.0, 12.0, 12.0]
    assert not insp._live_timer.isActive()           # fed by kubectl poll, not docker stats

    # Autoscaling Group resolves to the SAME deployment (it's the HPA on that Pod)
    assert insp._k8s_dep(w.ctx.topology.devices[asg]) == dep
    w.ctx.bus.selection_changed.emit(asg); app.processEvents()
    assert insp.metrics._d is insp._hist[dep]

    # the cluster is a real container -> cloud (docker-stats) layout
    w.ctx.bus.selection_changed.emit(cl); app.processEvents()
    assert insp.metrics._layout is CLOUD_LAYOUT


def test_k8s_console_targets_the_cluster_not_fabric(monkeypatch):
    """Logging into a K8s Cluster / Pod must NOT route through the (nonexistent) fabric
    container — that was the 'service fabric is not running' bug."""
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    import gini.services as svcs
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    cmds = []
    monkeypatch.setattr(svcs, "open_terminal",
                        lambda title, wd, cmd: (cmds.append(cmd), (True, "ok"))[1])
    cl = w.api.add_device("k8s_cluster")["id"]
    pod = w.api.add_device("pod")["id"]
    w.ctx.add_link(cl, pod)
    w._running = True
    w._workdir = "/tmp/lab"

    w._open_terminal(cl)
    assert "fabric" not in cmds[-1] and "k8s1" in cmds[-1] and cmds[-1].endswith("sh")
    w._open_terminal(pod)
    assert "fabric" not in cmds[-1] and "kubectl exec" in cmds[-1] and "deploy/pod1" in cmds[-1]


def test_inspector_pod_and_asg_have_live_sliders():
    """Pod gets a Replicas slider, Autoscaling Group a Target-CPU slider (live knobs)."""
    from PySide6.QtWidgets import QApplication, QSlider
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    pod = w.api.add_device("pod", x=60, y=60)["id"]
    asg = w.api.add_device("instance_group", x=240, y=60)["id"]
    app.processEvents()

    w.ctx.bus.selection_changed.emit(pod); app.processEvents()
    assert w.inspector.findChildren(QSlider)          # Replicas slider present

    w.ctx.bus.selection_changed.emit(asg); app.processEvents()
    assert w.inspector.findChildren(QSlider)          # Target-CPU slider present
