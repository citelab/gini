#!/usr/bin/env python3.7
# -*- coding: utf-8 -*-

from flask import Flask, request, jsonify, abort

from simplecloud import logger


# only the function run_app should be available to public
__all__ = ['run_app', 'current_host', 'current_port']


app = Flask(__name__)
current_host = None
current_port = 0
_cloud = None


@app.errorhandler(400)
def client_error(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(500)
def server_error(e):
    return jsonify(error=str(e)), 500


@app.route('/')
def index():
    result = []
    for rule in app.url_map.iter_rules():
        url = str(rule)
        endpoint = rule.endpoint
        result.append(url)
    result.sort(key=lambda x : x.split('/'))
    return jsonify({'endpoints': result})


@app.route('/api/v1.0/cloud/list', methods=['GET'])
def cloud_list_services():
    services = _cloud.list_services()
    return jsonify({'services': list(services)})


@app.route('/api/v1.0/cloud/config/<string:serviceid>', methods=['POST'])
def cloud_config_service(serviceid):
    if not request.json:
        abort(400, description='json expected')
    try:
        key = request.json['key']
        value = request.json['value']
        action = request.json.get('action', 'put')
        status = _cloud.registry_update(serviceid, key, value, action)
        return jsonify({'success': status})
    except KeyError:
        abort(400, description='missing parameter')


@app.route('/api/v1.0/cloud/scale/<string:serviceid>', methods=['POST'])
def cloud_scale_service(serviceid):
    if not request.json:
        abort(400, description='json expected')
    if 'size' not in request.json:
        abort(400, description='missing size')
    status = _cloud.scale_service(serviceid, request.json['size'])
    return jsonify({'success': status})


@app.route('/api/v1.0/cloud/services/<string:serviceid>', methods=['GET', 'DELETE'])
def cloud_service(serviceid):
    if request.method == 'GET':
        return jsonify(_cloud.info_service(serviceid))
    elif request.method == 'DELETE':
        status = _cloud.stop_service(serviceid)
        return jsonify({'success': status})


@app.route('/api/v1.0/cloud/start', methods=['POST'])
def cloud_start():
    if not request.json:
        abort(400, description='json expected')
    try:
        image = request.json['image']
        name = request.json['name']
        port = int(request.json['port'])
        scale = int(request.json.get('scale', 1))
        command = request.json.get('command', None)
        status = _cloud.start_service(image, name, port, scale, command)
        return jsonify({'success': status})
    except KeyError:
        abort(400, description='missing parameter')
    except ValueError:
        abort(400, description='invalid parameter type')


@app.route('/api/v1.0/sfc/init', methods=['POST'])
def sfc_init():
    status = _cloud.sfc_init()
    return jsonify({'success': status})


@app.route('/api/v1.0/sfc/nodes', methods=['GET', 'POST'])
def sfc_nodes():
    if request.method == 'GET':
        resp = _cloud.sfc_show_services()
        return jsonify(resp)
    elif request.method == 'POST':
        if not request.json:
            abort(400, description='json expected')
        try:
            status = True
            for node in request.json['nodes']:
                function = node['function']
                service_name = node['name']
                status = status and _cloud.sfc_add_service(function, service_name)
            return jsonify({'success': status})
        except KeyError:
            abort(400, description='missing parameter')


@app.route('/api/v1.0/sfc/nodes/<string:nodeid>', methods=['DELETE'])
def sfc_node_delete(nodeid):
    status = _cloud.sfc_remove_service(nodeid, force=True)
    return jsonify({'success': status})


@app.route('/api/v1.0/sfc/chains', methods=['GET', 'POST'])
def sfc_chains():
    if request.method == 'GET':
        resp = _cloud.sfc_show_chains()
        return jsonify(resp)
    elif request.method == 'POST':
        if not request.json:
            abort(400, description='json expected')
        try:
            status = True
            for chain in request.json['chains']:
                services = chain['services']
                status = status and (_cloud.sfc_create_chain(services) is not None)
            return jsonify({'success': status})
        except KeyError:
            abort(400, description='missing parameter')


@app.route('/api/v1.0/sfc/chains/<string:chainid>', methods=['DELETE'])
def sfc_chain_delete(chainid):
    status = _cloud.sfc_remove_chain(chainid, force=True)
    return jsonify({'success': status})


@app.route('/api/v1.0/sfc/paths', methods=['GET', 'POST', 'DELETE'])
def sfc_paths():
    if request.method == 'GET':
        resp = _cloud.sfc_show_paths()
        return jsonify(resp)
    elif request.method == 'POST':
        if not request.json:
            abort(400, description='json expected')
        try:
            status = True
            for path in request.json['paths']:
                src = path['src']
                dst = path['dst']
                chainid = path['chain']
                status = status and _cloud.sfc_create_path(src, dst, chainid)
            return jsonify({'success': status})
        except KeyError:
            abort(400, description='missing parameter')
    elif request.method == 'DELETE':
        if not request.json:
            abort(400, description='json expected')
        try:
            status = True
            for path in request.json['paths']:
                src = path['src']
                dst = path['dst']
                status = status and _cloud.sfc_remove_path(src, dst)
            return jsonify({'success': status})
        except KeyError:
            abort(400, description='missing parameter')


def run_app(cloud_instance, host='0.0.0.0', port=8080):
    global _cloud, current_host, current_port
    _cloud = cloud_instance
    current_host = host
    current_port = port
    logger.info(f'Starting REST application on {host}:{port}')
    app.run(host=host, port=port, threaded=True)

