import sys
import shutil
import os
import logging
import pickle
import subprocess
import re
import time
import json
import ConfigParser
import urllib2

from bottle import Bottle, run, urljoin, HTTPResponse, request

AGENT_CONFIG_SERVER = None
GATEWAY_IP          = None
GATEWAY_PORT        = None
BASE_PATH           = None

# agent pickle file path
pickle_path = '.bidders'
json_path   = '.config'

# agent base path
exec_base_path   = None
config_base_path = None
log_base_path    = None
bidders_config_base_path = None

# set up logging
logging.basicConfig(filename='agent_gateway.log',
        format='%(asctime)-15s %(levelname)s %(message)s',
        level=logging.DEBUG)
logger = logging.getLogger('agent_gateway')

# app bottle
app = Bottle()

# initialize bidder map
bidders = {}


def map_and_redirect(uri, name):
    """
        maps the name, sets the uri and raises a redirection
        otherwise returns the json result code
    """
    try :
        # try to map the name to the internal config name
        location = urljoin(
            AGENT_CONFIG_SERVER,
            uri % bidders[name]['agent_conf_name'])
    except :
        return  {
                'resultCode'        :    1,
                'resultDescription' :   'unable to map %s' % name
                }
        raise HTTPResponse(body=json.dumps(result), status=404,
                Content_Type='application/json')
    raise HTTPResponse("", status=302, Location=location)


@app.get('/v1/agents')
def get_agents():
    """
        returns the list of bidders
    """
    return '%s\n' % bidders.keys()

@app.get('/v1/agents/config')
def get_agents_config():
    bidders_list = bidders.keys()
    
    bidders_combo = ""
    for name in bidders_list:
        bidders_combo = bidders_combo + '<option value="' + name + '">' + name + '</option>'
    return '<html><head><meta charset="UTF-8"><title>RTBKit Gateway Agents Management</title><script src="http://code.jquery.com/jquery-1.9.0.min.js" type="text/javascript"></script><script type="text/javascript">var agentConfig=""; $(document).ready(function(){$("#getConfigBtn").click(function(){if($("#agentsList").val()){$.ajax({type: "post",url: "/v1/agents/" + $("#agentsList").val() + "/config",contentType:"application/json; charset=utf-8",dataType: "json",success: function(data){agentConfig=data; $("#requestJson").val(JSON.stringify(data, null, 2));}});}});$("#updateBtn").click(function() {if($("#agentsList").val() && $("#requestJson").val() && agentConfig != "") {$.ajax({type: "post",url: "/v1/agents/" + $("#agentsList").val() + "/restart?executable=basic_bidding_agent&N=eui1.rtb1." + agentConfig.account[0] + $("#agentsList").val() + "&B=/home/rtbkit/prod/rtb/configs/bootstrap.eui1.json",contentType: "application/json;charset=utf-8",dataType: "json",data: $("#requestJson").val(),success: function (data) {}});}});$("#stopBtn").click(function() {if ($("#agentsList").val()) {$.ajax({type: "post",url: "/v1/agents/" + $("#agentsList").val() + "/stop?signal=15",contentType: "application/json; charset=utf-8",dataType: "json",data: {},success: function(data) {agentConfig = data;$("#requestJson").val(JSON.stringify(data, null, 2));}});}});});</script></head><body><center><h1>EU Agents Management</h1></center><select id="agentsList">' + bidders_combo + '</select>&nbsp;&nbsp;<button id="getConfigBtn">Get Config</button>&nbsp;&nbsp;<button id="stopBtn">Stop</button><br/><br/><label for="requestJson">Request JSON</label> <br/><textarea id="requestJson" rows="40" cols="120"></textarea><br/><br/><button id="updateBtn">Update</button></body></html>'

@app.post('/v1/agents/<name>/restart')
def restart_bidder(name):
    """
        restarts a given agent
    """
    # make a copy of the json configuration file
    conf_file_name = os.path.join(bidders_config_base_path, '%s.conf.json' % name)
    try :
        with open(conf_file_name) as file:
            pass
    except IOError as e:
        result['resultCode'] = 6
        result['resultDescription'] = 'unable to open config file: {0}'.format(e.strerror)
        raise HTTPResponse(body=json.dumps(result), status=500, Content_Type='application/json')

    new_conf_file_name = os.path.join(bidders_config_base_path, '%s_%s.conf.json' % (name, time.strftime('%d.%m.%Y_%H.%M.%S')))
    try :
        shutil.copyfile(conf_file_name, new_conf_file_name)
    except IOError as e:
        result['resultCode'] = 6
        result['resultDescription'] = 'unable to copy config file: {0}'.format(e.strerror)
        raise HTTPResponse(body=json.dumps(result), status=500, Content_Type='application/json')

    stop_bidder(name)
    result = start_bidder(name)
    return result

@app.post('/v1/agents/<name>/config')
@app.get('/v1/agents/<name>/config')
def get_config(name):
    """
        redirects the call to the agent configuration service
        on /v1/agents/<name>/config for the given name
    
    return map_and_redirect('/v1/agents/%s/config', name)
    """
    proxy_support = urllib2.ProxyHandler({})
    opener = urllib2.build_opener(proxy_support)
    location = urljoin(
        AGENT_CONFIG_SERVER,
        '/v1/agents/%s/config' % bidders[name]['agent_conf_name'])
    logger.info('bringing up bidder config from: %s' % location)
    res = opener.open(location)
    data = res.read()
    raise HTTPResponse(data, status=200, Content_Type='application/json')

@app.post('/v1/agents/<name>/heartbeat')
def heartbeat(name):
    """
        redirects the call to the agent configuration service
        on /v1/agents/<name>/heartbeat for the given name
    """
    return map_and_redirect('/v1/agents/%s/heartbeat', name)

@app.get('/v1/agents/all')
def get_all():
    """
        redirects the call to the agent configuration service
        on /v1/agents/all
    """
    location = urljoin(AGENT_CONFIG_SERVER, '/v1/agents/all')
    raise HTTPResponse("", status=302, Location=location)

@app.post('/v1/agents/<name>/start')
def start_bidder(name):
    """
        Starts up a bidder using as the instance parameters
        the arguments passed in the query string
    """
    global _process_id
    result = {
            'resultCode'        :   0,
            'resultDescription' :   'ok'
    }

    if name in bidders :
        result['resultCode'] = 1
        result['resultDescription'] = 'agent already started'
        raise HTTPResponse(body=json.dumps(result), status=500,
                Content_Type='application/json')
    else :
        bidder = {}

    # save the executable name and external name
    bidder['bidder_name'] = name
    bidder['executable'] = request.query['executable']
    # save the params
    escape = lambda x : '"%s"' % x
    bidder['params'] = {
         k:escape(v) for k,v in request.query.iteritems()
            if k not in ('executable', )
    }

    # create a file with the json configuration
    conf_file_name = os.path.join(
        bidders_config_base_path, '%s.conf.json' % name)
    try :
        conf_file = open(conf_file_name, 'w')
        conf_file.write(json.dumps(request.json))
        conf_file.close()
    except IOError as e:
        result['resultCode'] = 6
        result['resultDescription'] = 'unable to create config file: {0}'.format(e.strerror)
        raise HTTPResponse(body=json.dumps(result), status=500,
                Content_Type='application/json')
    except ValueError as e:
        result['resultCode'] = 6
        result['resultDescription'] = 'unable to create config file with JSON error: {0}'.format(str(e))
        raise HTTPResponse(body=json.dumps(result), status=500,
                Content_Type='application/json')
    except :
        result['resultCode'] = 6
        result['resultDescription'] = 'unable to create config file with unexpected error: {0}'.format(sys.exc_info()[0])
        raise HTTPResponse(body=json.dumps(result), status=500,
                Content_Type='application/json')

    logger.info('bringing up bidder %s=%s' % (name, bidder))
    # set the args a list (popen expects them that way)
    arguments = []
    for k,v in bidder['params'].iteritems() :
        arguments.append('-%s' % k)
        arguments.append(v)

    exe = ['nohup']
    exe.append('./%s' % bidder['executable'])
    exe.extend(arguments)
    exe.append('-f')
    exe.append(conf_file_name)
    exe.append('-u')
    exe.append(name)
    exe.append('&')
    logger.info('executing : %s' % ' '.join(exe))

    # check the log file
    log_file_name = 'agent_%s_%s.log' % (name, time.strftime('%d.%m.%Y_%H.%M.%S'))
    log_path = os.path.join(log_base_path, log_file_name)
    #try to unlink and then relink
    try :
        os.unlink(
            os.path.join(log_base_path, 'agent_%s.log' % name))
    except :
        pass
    os.symlink(log_file_name,
            os.path.join(log_base_path, 'agent_%s.log' % name))

    log_file = open(log_path, 'w')
    # bring the process up
    try :
        proc = subprocess.Popen(
            ' '.join(exe),
            cwd=exec_base_path,
            shell=True,
            close_fds=True,
            stdout=log_file)
    except :
        result['resultCode'] = 3
        result['resultDescription'] = 'error executing agent: {0}'.format(sys.exc_info()[0])
        raise HTTPResponse(body=json.dumps(result), status=500,
                Content_Type='application/json')

    # read the pid, the one that proc returns belongs to the shell
    pid = None
    # give it some time to make sure the pid is there
    time.sleep(1)
    with open(log_path, 'r') as f:
        for line in f:
            m = re.match('pid:(?P<pid>\d+)', line)
            if m is not None:
                pid = int(m.group('pid'))
                break
    f.close()
    if pid is None :
        # something is not ok
        logger.error('unable to find pid, are you printing it?')
        result['resultCode'] = 4
        result['resultDescription'] = 'unable to find pid, are you printing it?'
        raise HTTPResponse(body=json.dumps(result), status=500,
                Content_Type='application/json')
    # check if the pid still exists, sometimes a bidder starts and aborts
    # right away.
    if not os.path.exists('/proc/%d' % pid):
        # something is not ok
        logger.error('did the process aborted?')
        result['resultCode'] = 5
        result['resultDescription'] = 'process id %d lost' % pid
        raise HTTPResponse(body=json.dumps(result), status=500,
                Content_Type='application/json')

    # save the pid for the new bidder
    bidder['pid']  = pid
    logger.info('pid is : %d' % int(pid))
    # the key stored by the agent configuration service
    # is a concatenation of the bidder name passed and the
    # pid for for process
    bidder['agent_conf_name'] = \
        '%s_%s' % (name, bidder['pid'])
    logger.info('bidder %s got pid %d' % (name, bidder['pid']))

    # save it
    bidders[name] = bidder
    # great, let's pickle the data
    try :
        f = open(os.path.join(pickle_path, str(bidders[name]['pid'])), 'wb')
        pickle.dump(bidders[name], f)
        f.close()
    except :
        result['resultCode'] = 2
        result['resultDescription'] = 'unable to pickle configuration'
        raise HTTPResponse(body=json.dumps(result), status=500,
                Content_Type='application/json')

    result['pid'] = int(pid)
    return result


@app.post('/v1/agents/<name>/stop')
def stop_bidder(name):
    """
        Stops a running agent
    """
    result = {
            'resultCode'        :   0,
            'resultDescription' :   'ok'
    }

    if name not in bidders :
        result['resultCode'] = 1
        result['resultDescription'] = 'bidder not running'
        raise HTTPResponse(body=json.dumps(result), status=404,
                Content_Type='application/json')

    logger.info('stopping bidder %s=%s' % (name, bidders[name]))

    pid = bidders[name]['pid']
    try :
        signal = 9
        if 'signal' in request.query :
            signal = int(request.query['signal'])
        os.kill(pid, signal)
        logger.info('signal %d sent to process with pid %d' % (signal, pid))
    except :
        result['resultCode'] = 2
        result['resultDescription'] = 'unable to kill process %s' % pid
        raise HTTPResponse(body=json.dumps(result), status=500,
                Content_Type='application/json')

    logger.info('agent %s with pid %d stopped' % (name, pid))

    # clean up
    del bidders[name]
    try :
        os.remove(os.path.join(pickle_path, str(pid)))
    except :
        result = {
            'resultCode'        :   3,
            'resultDescription' :   'unable to delete pickled data: {0}'.format(sys.exc_info()[0])
        }
        raise HTTPResponse(body=json.dumps(result), status=500,
                Content_Type='application/json')
    return result

@app.get('/v1/agents/<name>/status')
def get_status(name):
    """
        retrieves the status of a given agent
    """
    result = {
            'resultCode'        :   1,
            'resultDescription' :   'down'
    }

    if not name in bidders:
        raise HTTPResponse(body=json.dumps(result), status=404,
                Content_Type='application/json')

    pid = bidders[name]['pid']
    if not os.path.exists('/proc/%d' % pid):
        # something is not ok, the bidder aborted
        logger.error('did the process %d aborted?' % pid)
        # clean up
        try :
            os.remove(os.path.join(pickle_path, str(pid)))
        except :
            pass
        del bidders[name]
        result['resultCode'] = 2
        result['resultDescription'] = 'process id %d lost' % pid
        raise HTTPResponse(body=json.dumps(result), status=500,
                Content_Type='application/json')

    result['resultCode'] = 0
    result['resultDescription'] = 'up'
    return result


class application:
    '''
        Main app exposed
    '''

    def __init__(self, config_file):
        '''
            init everything
        '''
        self.app = app

        # load configuration
        self.config = ConfigParser.ConfigParser()
        self.config.read(config_file)

        global AGENT_CONFIG_SERVER
        global GATEWAY_IP
        global GATEWAY_PORT
        global BASE_PATH

        AGENT_CONFIG_SERVER = self.config.get('global', 'AGENT_CONFIG_SERVER')
        GATEWAY_IP = self.config.get('global', 'GATEWAY_IP')
        GATEWAY_PORT = int(self.config.get('global', 'GATEWAY_PORT'))
        BASE_PATH = self.config.get('global', 'BASE_PATH')

        logger.warning('AGENT_CONFIG_SERVER: %s' % AGENT_CONFIG_SERVER)
        logger.warning('GATEWAY_IP: %s' % GATEWAY_IP)
        logger.warning('GATEWAY_PORT: %d' % GATEWAY_PORT)
        logger.warning('BASE_PATH: %s' % BASE_PATH)

        global exec_base_path
        global config_base_path
        global log_base_path
        global bidders_config_base_path

        exec_base_path   = os.path.join(BASE_PATH, 'bin')
        config_base_path = BASE_PATH
        log_base_path    = os.path.join(BASE_PATH, 'logs')
        bidders_config_base_path = os.path.join(os.getcwd(), json_path)


        # check if the pickle_path exists
        if not os.path.exists(pickle_path):
              os.mkdir(pickle_path)
        # check if the json_path exists
        if not os.path.exists(json_path):
              os.mkdir(json_path)

        # for each pickled process reload the configuration
        for config in os.listdir(pickle_path):
            f = open(os.path.join(pickle_path, config), 'rb')
            c = pickle.load(f)
            bidders[c['bidder_name']] = c
            f.close()
            logger.warning('loaded agent %s=%s' % (c['bidder_name'], c))

    def run(self):
        '''
            run the development bottle server
        '''
        logger.warning('starting up server')
        run(self.app, host=GATEWAY_IP, port=GATEWAY_PORT, reloader=False)
