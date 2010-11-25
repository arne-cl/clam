#!/usr/bin/env python
#-*- coding:utf-8 -*-

###############################################################
# CLAM: Computational Linguistics Application Mediator
# -- CLAM Webservice --
#       by Maarten van Gompel (proycon)
#       http://ilk.uvt.nl/~mvgompel
#       Induction for Linguistic Knowledge Research Group
#       Universiteit van Tilburg
#       
#       Licensed under GPLv3
#
###############################################################

import web
import shutil
import os
import codecs
import subprocess
import glob
import sys
import datetime
import random
import re
import urllib2
import getopt
from copy import copy #shallow copy (use deepcopy for deep)
from functools import wraps

if __name__ == "__main__":
    sys.path.append(sys.path[0] + '/..')
    os.environ['PYTHONPATH'] = sys.path[0] + '/..'

import clam.common.status 
import clam.common.parameters
import clam.common.formats
import clam.common.digestauth
import clam.common.data
from clam.common.util import globsymlinks, setdebug, setlog, printlog, printdebug
import clam.config.defaults as settings #will be overridden by real settings later


#Maybe for later: HTTPS support
#web.wsgiserver.CherryPyWSGIServer.ssl_certificate = "path/to/ssl_certificate"
#web.wsgiserver.CherryPyWSGIServer.ssl_private_key = "path/to/ssl_private_key"


VERSION = '0.5.prealpha'

DEBUG = False
    
DATEMATCH = re.compile(r'^[\d\.\-\s:]*$')

setlog(sys.stdout)
#Empty defaults
#SYSTEM_ID = "clam"
#SYSTEM_NAME = "CLAM: Computional Linguistics Application Mediator"
#SYSTEM_DESCRIPTION = "CLAM is a webservice wrapper around NLP tools"
#COMMAND = ""
#ROOT = "."
#PARAMETERS = []
#URL = "http://localhost:8080"
#USERS = None


def error(msg):
    if __name__ == '__main__':
        print >>sys.stderr, "ERROR: " + msg
        sys.exit(1)
    else:
        raise Exception(msg) #Raise python errors if we were not directly invoked

def warning(msg):
    print >>sys.stderr, "WARNING: " + msg




TEMPUSER = '' #temporary global variable (not very elegant and not thread-safe!) #TODO: improve?
def userdb_lookup(user, realm):
    global TEMPUSER
    TEMPUSER = user
    return settings.USERS[user] #possible KeyError is captured by digest.auth itself!

#requirelogin = lambda x: x
#if settings.USERS:
#    requirelogin = clam.common.digestauth.auth(userdb_lookup, realm= settings.SYSTEM_ID)

auth = lambda x: x

#auth = clam.common.digestauth.auth(userdb_lookup, realm= settings.SYSTEM_ID)

def requirelogin(f):
    global TEMPUSER, auth
    def wrapper(*args, **kwargs):
        printdebug("wrapper:"+ repr(f))        
        args = list(args)
        args.append(TEMPUSER)
        args = tuple(args)
        if settings.USERS:
            #f = clam.common.digestauth.auth(userdb_lookup, realm=settings.SYSTEM_ID)(f)       
            return auth(f)(*args, **kwargs)
        else:
            return f(*args, **kwargs)
    return wraps(f)(wrapper)


class TestInterface(object):
    @requirelogin
    def GET(self, user = None):
        raise web.webapi.Forbidden('Test error response')
            

class CLAMService(object):
    """CLAMService is the actual service object. See the documentation for a full specification of the REST interface."""

    urls = (
        '/', 'Index',
        '/data.js', 'InterfaceData', #provides Javascript data for the web interface
        #'/t/', 'TestInterface',
        '/([A-Za-z0-9_]*)/?', 'Project',
        '/([A-Za-z0-9_]*)/upload/?', 'Uploader',
        '/([A-Za-z0-9_]*)/output/(.*)/?', 'OutputFileHandler', #(also handles viewers, convertors, metadata, and archive download
        '/([A-Za-z0-9_]*)/input/([^/]*)/?', 'InputFileHandler',
        #'/([A-Za-z0-9_]*)/output/([^/]*)/([^/]*)/?', 'ViewerHandler', #first viewer is always named 'view', second 'view2' etc..
    )

    def __init__(self, mode = 'standalone'):
        global VERSION
        printlog("Starting CLAM WebService, version " + str(VERSION) + " ...")
        if not settings.ROOT or not os.path.isdir(settings.ROOT):
            error("Specified root path " + settings.ROOT + " not found")
        elif not settings.COMMAND.split(" ")[0] or os.system("which " + settings.COMMAND.split(" ")[0] + "> /dev/null 2> /dev/null") != 0:
            error("Specified command " + settings.COMMAND.split(" ")[0] + " not found or without execute permission")
        elif not settings.PROFILES:
            error("No profiles were defined in settings module!")
        elif not settings.PARAMETERS:
            warning("No parameters defined in settings module!")
        else:      
            lastparameter = None      
            try:
                for parametergroup, parameters in settings.PARAMETERS:
                    for parameter in parameters:
                        assert isinstance(parameter, clam.common.parameters.AbstractParameter)
                        lastparameter = parameter
            except AssertionError:
                msg = "Syntax error in parameter specification."
                if lastparameter:            
                     msg += "Last part parameter: ", lastparameter.id
                error(msg)

        self.service = web.application(self.urls, globals())
        self.service.internalerror = web.debugerror
        self.mode = mode
        printlog("Server available on http://" + settings.HOST + ":" + str(settings.PORT) +'/')
        if mode == 'fastcgi':
            web.wsgi.runwsgi = lambda func, addr=None: web.wsgi.runfcgi(func, addr)
            self.service.run()
        elif mode == 'wsgi':
            self.application = self.service.wsgifunc()
        elif mode == 'standalone' or mode == 'cherrypy' or not mode:
            #standalone mode
            self.mode = 'standalone'
            self.service.run()
        else:
            raise Exception("Unknown mode: " + mode + ", specify 'fastcgi', 'wsgi' or 'standalone'")

    @staticmethod
    def corpusindex(): 
            """Get list of pre-installed corpora"""
            corpora = []
            for f in glob.glob(settings.ROOT + "corpora/*"):
                if os.path.isdir(f):
                    corpora.append(os.path.basename(f))
            return corpora


class Index(object):
    @requirelogin
    def GET(self, user = None):
        """Get list of projects"""
        projects = []
        for f in glob.glob(settings.ROOT + "projects/*"): #TODO LATER: Implement some kind of caching
            if os.path.isdir(f):
                d = datetime.datetime.fromtimestamp(os.stat(f)[8])  
                project = os.path.basename(f)
                if not settings.PROJECTS_PUBLIC or user in settings.ADMINS or user in Project.path(project):
                    projects.append( ( project , d.strftime("%Y-%m-%d %H:%M:%S") ) )

        errors = "no"
        errormsg = ""

        corpora = CLAMService.corpusindex()

        render = web.template.render('templates')

        url = 'http://' + settings.HOST
        if settings.PORT != 80:
            url += ':' + str(settings.PORT)
        if settings.URLPREFIX and settings.URLPREFIX != '/':
            if settings.URLPREFIX[0] != '/':
                url += '/'
            url += settings.URLPREFIX
        if url[-1] == '/': url = url[:-1]

        web.header('Content-Type', "text/xml; charset=UTF-8")
        return render.response(VERSION, settings.SYSTEM_ID, settings.SYSTEM_NAME, settings.SYSTEM_DESCRIPTION, user, None, url, -1 ,"",[],0, errors, errormsg, settings.PARAMETERS,corpora, None,None, settings.PROFILES, None, projects )
        


class Project(object):

    @staticmethod
    def users(project):
        path = Project.path(project)
        users = []
        if os.path.isfile(path + '.users'):
            f = codecs.open(path + '.users','r','utf-8')
            for user in f.readlines():
                if user.strip():
                    users.append(user.strip())
            f.close()
        return users
    

    @staticmethod
    def validate(project):
        return re.match(r'^\w+$',project, re.UNICODE)

    @staticmethod
    def path(project):
        """Get the path to the project (static method)"""
        return settings.ROOT + "projects/" + project + "/"

    @staticmethod
    def create(project, user):         
        """Create project skeleton if it does not already exist (static method)"""
        if not Project.validate(project):
            raise web.webapi.Forbidden('Invalid project ID')
        printdebug("Checking if " + settings.ROOT + "projects/" + project + " exists") 
        if not project:
            raise web.webapi.Forbidden('No project name') 
        if not os.path.isdir(settings.ROOT + "projects/" + project):
            printlog("Creating project '" + project + "'")
            os.mkdir(settings.ROOT + "projects/" + project)
            os.mkdir(settings.ROOT + "projects/" + project + "/input")
            os.mkdir(settings.ROOT + "projects/" + project + "/output")
            if not settings.PROJECTS_PUBLIC:
                f = codecs.open(settings.ROOT + "projects/" + project + '/.users','w','utf-8')                         
                f.write(user + "\n")
                f.close()
        else:
            #project already exists, pass silently
            pass

    @staticmethod
    def access(project, user):
        """Checks whether the specified user has access to the project"""
        userfile = Project.path(project) + ".users"
        if os.path.isfile(userfile):
            access = False
            f = codecs.open(userfile,'r','utf-8')
            for line in f:
                line = line.strip()
                if line and user == line.strip():
                    access = True
                    break
            f.close()
            return access
        else:
            return True #no access file, grant access for all users

    def pid(self, project):
        pidfile = Project.path(project) + '.pid'
        if os.path.isfile(pidfile):
            f = open(pidfile,'r')
            pid = int(f.read(os.path.getsize(pidfile)))
            f.close()
            return pid
        else:
            return 0

    def running(self,project):
        pid = self.pid(project)
        if pid == 0:
            return False
        #printdebug("Polling process " + str(pid) + ", still running?" ) 
        done = False
        statuscode = 0
        try:
            returnedpid, statuscode = os.waitpid(pid, os.WNOHANG)
            if returnedpid == 0:
                return True
        except OSError: #no such process
            done = True            
        if done or returnedpid == pid:
            if os.path.isfile(Project.path(project) + ".pid"):
                f = open(Project.path(project) + ".done",'w')
                f.write(str(statuscode)) #non-zero exit codes are interpreted as errors!
                f.close()
                os.unlink(Project.path(project) + ".pid")
            return False        
    
    def abort(self,project):
        if self.pid(project) == 0:
            return False
        try:
            printlog("Aborting process in project '" + project + "'" )
            os.kill(self.pid(project), 15)
            os.unlink(Project.path(project) + ".pid")
            return True
        except:
            return False  

    def done(self,project):
        return os.path.isfile(Project.path(project) + ".done")

    def exitstatus(self, project):
        f = open(Project.path(project) + ".done")
        status = int(f.read(1024))
        f.close()
        return status

    def preparingdownload(self,project):
        return os.path.isfile(Project.path(project) + ".download")

    def processingupload(self,project):
        return os.path.isfile(Project.path(project) + ".upload")

    def exists(self, project):
        """Check if the project exists"""
        printdebug("Checking if " + settings.ROOT + "projects/" + project + " exists") 
        return os.path.isdir(Project.path(project))

    def statuslog(self,project):
        statuslog = []
        statusfile = Project.path(project) + ".status"
        totalcompletion = 0
        if os.path.isfile(statusfile):
            prevmsg = None
            f = open(statusfile)
            for line in f:
                line = line.strip()
                if line:
                    message = ""
                    completion = 0
                    timestamp = ""
                    for field in line.split("\t"):
                        if field:
                            if field[-1] == '%' and field[:-1].isdigit():
                                completion = int(field[:-1])
                                if completion > 0:
                                    totalcompletion = completion
                            elif DATEMATCH.match(field):
                                if field.isdigit():     
                                      try:
                                          d = datetime.datetime.fromtimestamp(float(field))  
                                          timestamp = d.strftime("%d/%b/%Y %H:%M:%S")
                                      except:
                                          pass
                            else:
                                message += " " + field   

                    if message and (message != prevmsg):
                        #print "STATUSLOG: t=",timestamp,"c=",completion,"msg=" + message.strip()
                        statuslog.append( (message.strip(), timestamp, completion) )
                        prevmsg = message
            msg = f.read(os.path.getsize(statusfile))
            f.close()
            statuslog.reverse()
        return statuslog, totalcompletion

    def status(self, project):
        global DATEMATCH
        if self.running(project):
            statuslog, completion = self.statuslog(project)
            if statuslog:
                return (clam.common.status.RUNNING, statuslog[0][0],statuslog, completion)
            else:
                return (clam.common.status.RUNNING, "The system is running",  [], 0) #running
        elif self.done(project):
            statuslog, completion = self.statuslog(project)
            if statuslog:
                return (clam.common.status.DONE, statuslog[0][0],statuslog, completion)
            else:
                return (clam.common.status.DONE, "Done", statuslog, 100)
        else:
            return (clam.common.status.READY, "Accepting new input files and selection of parameters", [], 0)


    @staticmethod
    def inputindex(project, d = ''):
        prefix = Project.path(project) + 'input/'
        for f in glob.glob(prefix + d + "/*"):
            if os.path.basename(f)[0] != '.': #always skip all hidden files
                if os.path.isdir(f):
                    for result in Project.inputindex(project, f[len(prefix):]):
                        yield result
                else:   
                    yield clam.common.data.CLAMInputFile(Project.path(project), f[len(prefix):])


    @staticmethod
    def outputindex(project, d = ''):
        prefix = Project.path(project) + 'output/'
        for f in glob.glob(prefix + d + "/*"):
            if os.path.basename(f)[0] != '.': #always skip all hidden files
                if os.path.isdir(f):
                    for result in Project.outputindex(project, f[len(prefix):]):
                        yield result
                else:   
                    yield clam.common.data.CLAMOutputFile(Project.path(project), f[len(prefix):])

    @staticmethod
    def inputindexbytemplate(project, inputtemplate):
        """Retrieve sorted index for the specified input template"""
        index = []
        prefix = Project.path(project) + 'input/'
        for linkf, f in globsymlinks(prefix + '.*.INPUTTEMPLATE.' + inputtemplate.id + '.*'):
            seq = int(linkf.split('.')[-1])
            index.append( (seq,f) )
            
        #yield CLAMFile objects in proper sequence
        for seq, f in sorted(index):
            yield seq, clam.common.data.CLAMInputFile(Project.path(project), f[len(prefix):])
            
            
    @staticmethod
    def outputindexbytemplate(project, outputtemplate):
        """Retrieve sorted index for the specified input template"""
        index = []
        prefix = Project.path(project) + 'output/'
        for linkf, f in globsymlinks(prefix + '.*.OUTPUTTEMPLATE.' + outputtemplate.id + '.*'):
            seq = int(linkf.split('.')[-1])
            index.append( (seq,f) )
            
        #yield CLAMFile objects in proper sequence
        for seq, f in sorted(index):
            yield seq, clam.common.data.CLAMOutputFile(Project.path(project), f[len(prefix):])
                        


    def response(self, user, project, parameters, errormsg = "", datafile = False):
        global VERSION

        #check if there are invalid parameters:
        if not errormsg:
            errors = "no"
        else:
            errors = "yes"

        statuscode, statusmsg, statuslog, completion = self.status(project)
        

        corpora = []
        if statuscode == clam.common.status.READY:
            corpora = CLAMService.corpusindex()
        else:
            corpora = []
        if statuscode == clam.common.status.DONE:
            outputpaths = Project.outputindex(project)
            if self.exitstatus(project) != 0: #non-zero codes indicate errors!
                errors = "yes"
                errormsg = "An error occurred within the system. Please inspect the error log for details"
                printlog("Child process failed, exited with non zero-exit code.")
        else:
            outputpaths = []        
        if statuscode == clam.common.status.READY:
            inputpaths = Project.inputindex(project)
        else:
            inputpaths = []      
        
        for parametergroup, parameterlist in parameters:
            for parameter in parameterlist:
                if parameter.error:
                    errors = "yes"
                    if not errormsg: errormsg = "One or more parameters are invalid"
                    printlog("One or more parameters are invalid")
                    break

        render = web.template.render('templates')
        
        url = 'http://' + settings.HOST
        if settings.PORT != 80:
            url += ':' + str(settings.PORT)
        if settings.URLPREFIX and settings.URLPREFIX != '/':
            if settings.URLPREFIX[0] != '/':
                url += '/'
            url += settings.URLPREFIX
        if url[-1] == '/': url = url[:-1]

        web.header('Content-Type', "text/xml; charset=UTF-8")
        return render.response(VERSION, settings.SYSTEM_ID, settings.SYSTEM_NAME, settings.SYSTEM_DESCRIPTION, user, project, url, statuscode,statusmsg, statuslog, completion, errors, errormsg, parameters,corpora, outputpaths,inputpaths, settings.PROFILES, datafile, None )
        
                    
    @requirelogin
    def GET(self, project, user=None):
        """Main Get method: Get project state, parameters, outputindex"""
        if not self.exists(project):
            raise web.webapi.NotFound("Project " + project + " was not found") #404
        else:
            if user and not Project.access(project, user):
                raise web.webapi.Unauthorized("Access denied to project " + project + " for user " + user) #401
            return self.response(user, project, settings.PARAMETERS) #200


    @requirelogin
    def PUT(self, project, user=None):
        """Create an empty project"""
        Project.create(project, user)
        raise web.webapi.Created("Project " + project + " has been created", {'Location': project + '/'}) #201

    @requirelogin
    def POST(self, project, user=None):  
        Project.create(project, user)
        if user and not Project.access(project, user):
            raise web.webapi.Unauthorized("Access denied to project " + project + " for user " + user) #401
                    
        #Generate arguments based on POSTed parameters
        params = []
        postdata = web.input()
        errors = False

        #we're going to modify parameter values, this we can't do
        #on the global variable, that won't be thread-safe, we first
        #make a (shallow) copy and act on that  
        parameters = []
        for parametergroup, parameterlist in settings.PARAMETERS:
            newparameterlist = []
            for parameter in parameterlist:
                newparameterlist.append(copy(parameter))
            parameters.append( (parametergroup, newparameterlist) ) 

        for parametergroup, parameterlist in parameters:
            for parameter in parameterlist:
                if parameter.access(user):
                    postvalue = parameter.valuefrompostdata(postdata) #parameter.id in postdata and postdata[parameter.id] != '':    
                    if not (isinstance(postvalue,bool) and postvalue == False):
                        if parameter.set(postvalue): #may generate an error in parameter.error
                            params.append(parameter.compilearg(parameter.value))
                        else:
                            if not parameter.error: parameter.error = "Something went wrong whilst setting this parameter!" #shouldn't happen
                            printlog("Unable to set " + parameter.id + ": " + parameter.error)
                            errors = True
                    elif parameter.required:
                        #Not all required parameters were filled!
                        parameter.error = "This option must be set"
                        errors = True
                    if parameter.value and (parameter.forbid or parameter.require):
                        for _, parameterlist2 in parameters:
                            for parameter2 in parameterlist2:
                                    if parameter.forbid and parameter2.id in parameter.forbid and parameter2.value:
                                        parameter.error = parameter2.error = "Setting parameter '" + parameter.name + "' together with '" + parameter2.name + "'  is forbidden"
                                        printlog("Setting " + parameter.id + " and " + parameter2.id + "' together is forbidden")
                                        errors = True
                                    if parameter.require and parameter2.id in parameter.require and not parameter2.value:
                                        parameter.error = parameter2.error = "Parameters '" + parameter.name + "' has to be set with '" + parameter2.name + "'  is"
                                        printlog("Setting " + parameter.id + " requires you also set " + parameter2.id )
                                        errors = True
                                        
        url = 'http://' + settings.HOST
        if settings.PORT != 80:
            url += ':' + str(settings.PORT)
        if settings.URLPREFIX and settings.URLPREFIX != '/':
            if settings.URLPREFIX[0] != '/':
                url += '/'
            url += settings.URLPREFIX
        if url[-1] == '/': url = url[:-1]                                        

        if not errors: #We don't even bother running the profiler if there are errors
            matchedprofiles = clam.common.data.profiler(settings.PROFILES, Project.path(project), parameters, settings.SYSTEM_ID, settings.SYSTEM_NAME, url)

        if errors:
            #There are parameter errors, return 403 response with errors marked
            printlog("There are parameter errors, not starting.")
            raise web.webapi.Forbidden(unicode(self.response(user, project, parameters)))
        elif not matchedprofiles:
            printlog("No profiles matching, not starting.")
            raise web.webapi.Forbidden(unicode(self.response(user, project, parameters, "No profiles matching input and parameters, unable to start. Are you sure you added all necessary input files and set all necessary parameters?")))
        else:
            #write clam.xml output file
            render = web.template.render('templates')
            f = open(Project.path(project) + "clam.xml",'w')
            f.write(str(self.response(user, project, parameters, "",True)))
            f.close()



            #Start project with specified parameters
            cmd = settings.COMMAND
            cmd = cmd.replace('$PARAMETERS', " ".join(params))
            if 'usecorpus' in postdata and postdata['usecorpus']:
                corpus = postdata['usecorpus'].replace('..','') #security            
                #use a preinstalled corpus:
                if os.path.exists(settings.ROOT + "corpora/" + corpus):
                    cmd = cmd.replace('$INPUTDIRECTORY', settings.ROOT + "corpora/" + corpus + "/")
                else:
                    raise web.webapi.NotFound("Corpus " + corpus + " not found")
            else:
                cmd = cmd.replace('$INPUTDIRECTORY', Project.path(project) + 'input/')
            cmd = cmd.replace('$OUTPUTDIRECTORY',Project.path(project) + 'output/')
            cmd = cmd.replace('$STATUSFILE',Project.path(project) + '.status')
            cmd = cmd.replace('$DATAFILE',Project.path(project) + 'clam.xml')
            cmd = cmd.replace('$USERNAME',user if user else "anonymous")
            #cmd = sum([ params if x == "$PARAMETERS" else [x] for x in COMMAND ] ),[])
            #cmd = sum([ Project.path(project) + 'input/' if x == "$INPUTDIRECTORY" else [x] for x in COMMAND ] ),[])        
            #cmd = sum([ Project.path(project) + 'output/' if x == "$OUTPUTDIRECTORY" else [x] for x in COMMAND ] ),[])        
            #TODO: protect against insertion
            if settings.COMMAND.find("2>") == -1:
                cmd += " 2> " + Project.path(project) + "output/error.log" #add error output
            printlog("Starting " + settings.COMMAND + ": " + repr(cmd) + " ..." )
            process = subprocess.Popen(cmd,cwd=Project.path(project), shell=True)				
            if process:
                pid = process.pid
                printlog("Started with pid " + str(pid) )
                f = open(Project.path(project) + '.pid','w')
                f.write(str(pid))
                f.close()
                raise web.webapi.Accepted(unicode(self.response(user, project, parameters))) #returns 202 - Accepted
            else:
                raise web.webapi.InternalError("Unable to launch process")

    @requirelogin
    def DELETE(self, project, user=None):
        if not self.exists(project):
            raise web.webapi.NotFound("No such project: " + project)
        statuscode, _, _, _  = self.status(project)
        if statuscode == clam.common.status.RUNNING:
            self.abort(project)   
        printlog("Deleting project '" + project + "'" )
        shutil.rmtree(Project.path(project))
        return "Project deleted: " + project #200

class OutputFileHandler(object):

    @requirelogin
    def GET(self, project, filename, user=None):    
        raw = filename.split('/')

        viewer = None
        
        if filename.strip('/') == "":
            #this is a request for everything
            for line in self.getarchive(project):
                yield line
        elif len(raw) >= 2 and raw[-1].lower() == 'metadata':
            #this is a request for metadata
            filename = "/".join(raw[:-1])
            viewer = 'metadata'
        elif len(raw) >= 2:
            #this may be a viewer request, check and set viewer=
            #TODO: Re-implement viewer support!
            raise NotImplementedError
            
        try:
            outputfile = clam.common.data.CLAMOutputFile(Project.path(project), filename)
        except:
            raise web.webapi.NotFound()
            
        if viewer:
            if viewer == 'metadata':
                if outputfile.metadata:
                    web.header('Content-Type', 'text/xml')
                    for line in outputfile.metadata.xml().split("\n"):
                        yield line                    
                else:
                    raise web.webapi.NotFound("No metadata found!")
            else:
                #TODO: Re-implement viewer support!
                raise NotImplementedError                
        else:
            if outputfile.metadata and outputfile.metadata.mimetype:
                web.header('Content-Type', outputfile.metadata.mimetype)
            for line in outputfile:
                yield line
            
            
    def DELETE(self, project, filename, user=None):    
        """Delete an output file"""
        
        filename = filename.replace("..","") #Simple security
        
        if len(filename) == 0:
            #Deleting all output files and resetting
            self.reset(project)
            return "Deleted" #200
        elif os.path.isdir(Project.path(project) + filename): 
            #Deleting specified directory
            shutil.rmtree(Project.path(project) + filename)
            return "Deleted" #200
        else:
            try:
                file = clam.common.data.CLAMOutputFile(Project.path(project), filename)
            except:
                raise web.webapi.NotFound()
                
            success = file.delete()            
            if not success:
                raise web.webapi.NotFound()
            else:
                return "Deleted" #200    


    def reset(self, project):
        """Reset system, delete all output files and prepare for a new run"""
        d = Project.path(project) + "output"
        if os.path.isdir(d):
            shutil.rmtree(d)
            os.mkdir(d)
        else:
            raise web.webapi.NotFound()
        if os.path.exists(Project.path(project) + ".done"):
            os.unlink(Project.path(project) + ".done")                       
        if os.path.exists(Project.path(project) + ".status"):
            os.unlink(Project.path(project) + ".status")        

    def getarchive(self, project):
        """Generates and returns a download package (or 403 if one is already in the process of being prepared)"""
        if os.path.isfile(Project.path(project) + '.download'):
            #make sure we don't start two compression processes at the same time
            raise web.forbidden()
        else:
            data = web.input()
            if 'format' in data:
                format = data['format']
            else:
                format = 'zip' #default

            #validation, security
            contentencoding = None
            if format == 'zip':
                contenttype = 'application/zip'
                command = "/usr/bin/zip -r" #TODO: do not hard-code path!
                if os.path.isfile(Project.path(project) + "output/" + project + ".tar.gz"):
                    os.unlink(Project.path(project) + "output/" + project + ".tar.gz")
                if os.path.isfile(Project.path(project) + "output/" + project + ".tar.bz2"):
                    os.unlink(Project.path(project) + "output/" + project + ".tar.bz2")
            elif format == 'tar.gz':
                contenttype = 'application/x-tar'
                contentencoding = 'gzip'
                command = "/bin/tar -czf"
                if os.path.isfile(Project.path(project) + "output/" + project + ".zip"):
                    os.unlink(Project.path(project) + "output/" + project + ".zip")
                if os.path.isfile(Project.path(project) + "output/" + project + ".tar.bz2"):
                    os.unlink(Project.path(project) + "output/" + project + ".tar.bz2")
            elif format == 'tar.bz2': 
                contenttype = 'application/x-bzip2'
                command = "/bin/tar -cjf"
                if os.path.isfile(Project.path(project) + "output/" + project + ".tar.gz"):
                    os.unlink(Project.path(project) + "output/" + project + ".tar.gz")
                if os.path.isfile(Project.path(project) + "output/" + project + ".zip"):
                    os.unlink(Project.path(project) + "output/" + project + ".zip")
            else:
                raise web.webapi.Forbidden('Invalid archive format') #TODO: message won't show

            path = Project.path(project) + "output/" + project + "." + format
            
            if not os.path.isfile(path):
                printlog("Building download archive in " + format + " format")
                cmd = command + ' ' + project + '.' + format + ' *'
                printdebug(cmd)
                printdebug(Project.path(project)+'output/')
                process = subprocess.Popen(cmd, cwd=Project.path(project)+'output/', shell=True)	        			
                if not process:
                    raise web.webapi.InternalError("Unable to make download package")                
                else:
                    pid = process.pid
                    f = open(Project.path(project) + '.download','w') 
                    f.write(str(pid))
                    f.close()
                    os.waitpid(pid, 0) #wait for process to finish
                    os.unlink(Project.path(project) + '.download')

            if contentencoding:
                web.header('Content-Encoding', contentencoding)
            web.header('Content-Type', contenttype)
            for line in open(path,'r'):
                yield line
        

class ViewerHandler(object):

    def view(self, project, filename, viewer_id):
        format = clam.common.formats.Format() #unspecified format
        for fmt in settings.OUTPUTFORMATS:
            if fmt.match(filename):
                format = fmt
                break
        viewer = None
        for i,v in enumerate(format.viewers):
            if (viewer_id == v.id or viewer_id.lower() == v.__class__.__name__.lower() or (i == 0 and viewer_id == 'view') or (viewer_id == 'view' + str(i + 1))):
                viewer = v
                break

        if viewer and os.path.exists(Project.path(project)):
            file = clam.common.data.CLAMOutputFile(Project.path(project), filename.replace('..',''), format)
        else:
            print "Viewer not found: ", project,filename,viewer_id
            raise web.webapi.NotFound("Viewer not found")

        data = web.input() #both for GET and POST
        return viewer.view(file, **data)


    @requirelogin
    def GET(self, project, filename, viewer_id, user=None):
        return self.view(project, filename, viewer_id)

    @requirelogin
    def POST(self, project, filename, viewer_id, user=None):
        return self.view(project, filename, viewer_id)

class InputFileHandler(object):

    @requirelogin
    def GET(self, project, filename, user=None):    
        """Download an input file"""
        
        if os.path.basename(filename.rstrip('/')).lower() == 'metadata':
            #this is a request for metadata? using filename/metadata ?
            filename = filename.rstrip('/')[:-9]
            metafilename = os.path.dirname(filename) 
            if metafilename: metafilename += '/'
            metafilename += '.' + os.path.basename(filename) + '.METADATA'
            filename = metafilename
 
        path = Project.path(project) + "input/" + filename.replace("..","")

        
        if os.path.isfile(path): 
            for line in open(path,'r'): 
                yield line
        elif os.path.isdir(path): 
            for f in glob.glob(path + "/*"):
                yield os.path.basename(f)                
        else:
            raise web.webapi.NotFound()

    @requirelogin
    def DELETE(self, project, filename, user=None):    
        """Delete an input file"""
        
        filename = filename.replace("..","") #Simple security
        
        if len(filename) == 0:
            #Deleting all input files
            shutil.rmtree(Project.path(project) + 'input')
            os.mkdir(Project.path(project) + 'input') #re-add new input directory
            return "Deleted" #200
        elif os.path.isdir(Project.path(project) + filename): 
            #Deleting specified directory
            shutil.rmtree(Project.path(project) + filename)
            return "Deleted" #200
        else:
            try:
                file = clam.common.data.CLAMInputFile(Project.path(project), filename)
            except:
                raise web.webapi.NotFound()
                
            success = file.delete()            
            if not success:
                raise web.webapi.NotFound()
            else:
                return "Deleted" #200

    @requirelogin
    def POST(self, project, filename, user=None): 
        """Add a new input file, this invokes the actual uploader"""

        #TODO LATER: add support for uploading metadata files
        #TODO LATER: re-add support for archives
        
        postdata = web.input(file={})
        inputtemplate = None
        
        if 'inputtemplate' in postdata:
            #An input template must always be provided            
            for profile in settings.PROFILES:
                for t in profile.input:
                    if t.id == postdata['inputtemplate']:
                        inputtemplate = t
            if not inputtemplate:
                #Inputtemplate not found, send 404 - Bad Request
                printlog("Specified inputtemplate (" + postdata['inputtemplate'] + ") not found!")
                raise web.webapi.notfound("Specified inputtemplate (" + postdata['inputtemplate'] + ") not found!")
        else:
            #Check if the specified filename can be uniquely associated with an inputtemplate
            for profile in settings.PROFILES:
                for t in profile.input:
                    if t.filename == filename:
                        if inputtemplate:
                            #we found another one, not unique!! reset and break
                            inputtemplate = None
                            break
                        else:
                            #good, we found one, don't break cause we want to make sure there is only one
                            inputtemplate = t                        
            if not inputtemplate:
                printlog("No inputtemplate specified and filename does not uniquely match with any inputtemplate!")
                raise web.webapi.NotFound("No inputtemplate specified nor auto-detected for this filename!")
                
            
            
        #See if other previously uploaded input files use this inputtemplate
        if inputtemplate.unique:
            nextseq = 0 #unique
        else:
            nextseq = 1 #will hold the next sequence number for this inputtemplate (in multi-mode only)
            
        for seq, inputfile in Project.inputindexbytemplate(project, inputtemplate):
            if inputtemplate.unique:
                raise web.webapi.Forbidden("You have already submitted a file of this type, you can only submit one. Delete it first. (Inputtemplate=" + inputtemplate.id + ", unique=True)") #(it will have to be explicitly deleted by the client first)
            else:
                if seq >= nextseq:
                    nextseq = seq + 1 #next available sequence number
            

        if not filename:
            if inputtemplate.filename:
                filename = inputtemplate.filename
            elif inputtemplate.extension:
                filename = str(nextseq) +'-' + str("%034x" % random.getrandbits(128)) + '.' + inputtemplate.extension
            else:
                filename = str(nextseq) +'-' + str("%034x" % random.getrandbits(128)) 
                
        if inputtemplate.filename:
            if filename != inputtemplate.filename:
                raise web.webapi.Forbidden("Specified filename must the filename dictated by the inputtemplate, which is " + inputtemplate.filename)
            #TODO LATER: add support for calling this with an actual number instead of #
        if inputtemplate.extension:
            if filename[-len(inputtemplate.extension) - 1:].lower() == '.' + inputtemplate.extension.lower():
                #good, extension matches (case independent). Let's just make sure the case is as defined exactly by the inputtemplate
                filename = filename[:-len(inputtemplate.extension) - 1] +  '.' + inputtemplate.extension
            else:
                raise web.webapi.Forbidden("Specified filename does not have the extention dictated by the inputtemplate ("+inputtemplate.textension+")") #403
            

        #Very simple security, prevent breaking out the input dir
        filename = filename.replace("..","")


        #Create the project (no effect if already exists)
        Project.create(project, user)        

        if not inputtemplate.unique:
            if '#' in filename: #resolve number in filename
                filename = filename.replace('#',str(nextseq))
        

        
        
        web.header('Content-Type', "text/xml; charset=UTF-8")
        output = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        output += "<clamupload>\n"
        
        if 'file' in postdata and (not isinstance(postdata['file'], dict) or len(postdata['file']) > 0):
            printlog("Adding client-side file " + postdata['file'].filename + " to input files")            
            sourcefile = postdata['file'].filename
        elif 'url' in postdata and postdata['url']:
            #Download from URL
            printlog("Adding web-based URL " + postdata['url'] + " to input files")
            sourcefile = postdata['url']    
        elif 'contents' in postdata and postdata['contents']:
            #In message
            printlog("Adding file " + filename + " with explicitly provided contents to input files")
            sourcefile = "editor"
        else:
            raise web.webapi.Forbidden("No file, url or contents specified!")
            
        output += "<upload source=\""+sourcefile +"\" filename=\""+filename+"\" inputtemplate=\"" + inputtemplate.id + "\" templatelabel=\""+inputtemplate.label+"\" >\n"

          
        #============================ Generate metadata ========================================
        printdebug('(Generating and validating metadata)')
        errors, parameters = inputtemplate.validate(postdata, user)
        
        
        if not errors:
            output += "<parameters errors=\"no\">"
        else:
            output += "<parameters errors=\"yes\">"
        for parameter in parameters:
            output += parameter.xml()
        output += "</parameters>"
            
        if not errors:
            #============================ Transfer file ========================================
            printdebug('(Start file transfer: ' +  Project.path(project) + 'input/' + filename+' )')
            if 'file' in postdata and (not isinstance(postdata['file'], dict) or len(postdata['file']) > 0):
                #Upload file from client to server
                f = open(Project.path(project) + 'input/' + filename,'wb')
                for line in postdata['file'].file:
                    f.write(line) #encoding unaware, seems to solve big-file upload problem
                f.close()            
            elif 'url' in postdata and postdata['url']:
                #Download file from 3rd party server to CLAM server
                try:
                    req = urllib2.urlopen(postdata['url'])
                except:
                    raise web.webapi.NotFound()
                CHUNK = 16 * 1024
                f = open(Project.path(project) + 'input/' + filename,'wb')
                while True:
                    chunk = req.read(CHUNK)
                    if not chunk: break
                    f.write(chunk)     
                f.close()                                  
            elif 'contents' in postdata and postdata['contents']:  
                #Contents passed in POST message itself
                f = codecs.open(Project.path(project) + 'input/' + filename,'wb')
                f.write(postdata['contents'])
                f.close()
            
            
        printdebug('(File transfer completed)')
        
        #Create a file object
        file = clam.common.data.CLAMInputFile(Project.path(project), filename, False) #get CLAMInputFile without metadata (chicken-egg problem, this does not read the actual file contents!
        
        #============== Generate metadata ==============

        try:
            #Now we generate the actual metadata object (unsaved yet though). We pass our earlier validation results to prevent computing it again
            validmeta, metadata, parameters = inputtemplate.generate(file, (errors, parameters ))
            if validmeta:
                #And we tie it to the CLAMFile object
                file.metadata = metadata
                #Add inputtemplate ID to metadata
                metadata.inputtemplate = inputtemplate.id
            
        except ValueError, KeyError:
            validmeta = False
            
        if not validmeta:    
            output += "<metadataerror />" #This usually indicates an error in service configuration!
        else:                    
            #====================== Validate the file itself ====================
            valid = file.validate()        
            
            if valid:                       
                output += "<valid>yes</valid>"                
                                
                #Great! Everything ok, save metadata
                metadata.save(Project.path(project) + 'input/' + file.metafilename())
                
                #And create symbolic link for inputtemplates
                linkfilename = os.path.dirname(filename) 
                if linkfilename: linkfilename += '/'
                linkfilename += '.' + os.path.basename(filename) + '.INPUTTEMPLATE' + '.' + inputtemplate.id + '.' + str(nextseq)
                os.symlink(Project.path(project) + 'input/' + filename, Project.path(project) + 'input/' + linkfilename)
            else:
                #Too bad, everything worked out but the file itself doesn't validate.
                output += "<valid>no</valid>"
            
                #remove upload
                os.unlink(Project.path(project) + 'input/' + filename)
        
        
        output += "</upload>\n"       

        output += "</clamupload>"
        return output #200


class InterfaceData(object):
    """Provides Javascript data needed by the webinterface. Such as JSON data for the inputtemplates"""

    @requirelogin
    def GET(self, project, user=None):
        web.header('Content-Type', 'application/javascript')
        inputtemplates = []
        for profile in settings.PROFILES:
            for inputtemplate in profile.input:
                if not inputtemplate in inputtemplates: #no duplicates
                    inputtemplates.append( inputtemplate.json() )

        url = 'http://' + settings.HOST
        if settings.PORT != 80:
            url += ':' + str(settings.PORT)
        if settings.URLPREFIX and settings.URLPREFIX != '/':
            if settings.URLPREFIX[0] != '/':
                url += '/'
            url += settings.URLPREFIX
        if url[-1] == '/': url = url[:-1]

        return "baseurl = '" + url + "';\n inputtemplates = [ " + ",".join(inputtemplates) + " ];"

        
#class Uploader(object): #OBSOLETE!

    #def path(self, project):
        #return Project.path(project) + 'input/'

    #def isarchive(self,filename):
        #return (filename[-3:] == '.gz' or filename[-4:] == '.bz2' or filename[-4:] == '.zip')

    #def extract(self,project,filename, format):
        #namelist = None
        #subfiles = []
        #if filename[-7:].lower() == '.tar.gz':
            #cmd = 'tar -xvzf'
            #namelist = 'tar'
        #elif filename[-7:].lower() == '.tar.bz2':
            #cmd = 'tar -xvjf'
            #namelist = 'tar'
        #elif filename[-3:].lower() == '.gz':
            #cmd = 'gunzip'
            #subfiles = [filename[-3:]]  #one subfile only
        #elif filename[-4:].lower() == '.bz2':
            #cmd = 'bunzip2'
            #subfiles = [filename[-3:]] #one subfile only
        #elif filename[-4:].lower() == '.tar':
            #cmd = 'tar -xvf'
            #namelist = 'tar'
        #elif filename[-4:].lower() == '.zip':
            #cmd = 'unzip -u'
            #namelist = 'zip'
        #else:
            #raise Exception("Invalid archive format") #invalid archive, shouldn't happend

        #printlog("Extracting '" + filename + "'" )            
        #try:
            #process = subprocess.Popen(cmd + " " + filename, cwd=self.path(project), stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        #except:
            #raise web.webapi.InternalError("Unable to extract file: " + cmd + " " + filename + ", cwd="+ self.path(project))       
        #out, err = process.communicate() #waits for process to end 

        #if namelist:
            #firstline = True
            #for line in out.split("\n"):    
                #line = line.strip()        
                #if line:
                    #subfile = None
                    #if namelist == 'tar':
                        #subfile = line
                    #elif namelist == 'zip' and not firstline: #firstline contains archive name itself, skip it
                        #colon = line.find(":")
                        #if colon:
                            #subfile =  line[colon + 1:].strip()
                    #if subfile and os.path.exists(self.path(project) + subfile):
                        #newsubfile = format.filename(subfile)
                        #os.rename(self.path(project) + subfile, self.path(project) + newsubfile)
                        #subfiles.append(newsubfile)
                #firstline = False

        #return [ subfile for subfile in subfiles ] #return only the files that actually exist
        


    #def test(self,project, filename, inputformat, depth = 0):
        #printdebug("Testing " + filename)
        #o = ""       


        #if depth > 3: #security against archive-bombs
            #if os.path.exists(self.path(project) + filename):
                #os.unlink(self.path(project) + filename)
            #return ""

        #prefix = (depth + 1) * "\t"
        #remove = False
        #o += prefix + "<file name=\""+filename+"\""
        #if not os.path.exists(self.path(project) + filename):
            #o += " uploaded=\"no\" />\n"
        #else:
            #if self.isarchive(filename):
                #o += " archive=\"yes\">"
                #remove = True #archives no longer necessary after extract
            #else:
                #o += " format=\""+inputformat.__class__.__name__+"\" formatlabel=\""+inputformat.name+"\" encoding=\""+inputformat.encoding+"\""; #TODO: output nice format labels?
                #if inputformat.validate(self.path(project) + filename):
                    #o += " validated=\"yes\" />\n"
                    #printlog("Succesfully validated '" + filename + "'" )
                #else:
                    #o += " validated=\"no\" />\n"
                    #printlog("File did not validate '" + filename + "'" )
                    #remove = True #remove files that don't validate
            
            #if self.isarchive(filename):            
                #for subfilename in self.extract(project,filename, inputformat):
                    #if subfilename[-1] != '/': #only act on files, not directories
                        #printdebug("Extracted from archive: " + subfilename)
                        #if not inputformat.archivesubdirs and os.path.dirname(subfilename) != '':
                            ##we don't want subdirectories, move the files:
                            ##TODO: delete subdirectories
                            #printdebug("Moving extracted file out of subdirectory...")
                            #os.rename(self.path(project) + subfilename, self.path(project) + os.path.basename(subfilename))
                            #o += self.test(project,os.path.basename(subfilename), inputformat, depth + 1)
                        #else:
                            #o += self.test(project,subfilename, inputformat, depth + 1)
                #o += prefix + "</file>\n"    

        #if remove and os.path.exists(self.path(project) + filename):
           #printdebug("Removing '" + filename + "'" )
           #os.unlink(self.path(project) + filename)

        #return o


    #@requirelogin
    #def POST(self, project, user=None): #OBSOLETE!
        ##postdata = web.input()

        ##defaults (max 25 uploads)
        #kwargs = {}
        #for i in range(1,26):    
            #kwargs['upload' + str(i)] = {}                            
        #postdata = web.input(**kwargs)
        #if not 'uploadcount' in postdata or not postdata['uploadcount'].isdigit():
            #raise BadRequest('No valid uploadcount specified') #TODO: message doesn't show to client
        #if int(postdata['uploadcount']) > 25:
            #raise BadRequest('Too many uploads') #TODO: message doesn't show to client

        ##Check if all uploads have a valid format specified, raise 403 otherwise, dismissing any uploads
        #for i in range(1,int(postdata['uploadcount']) + 1):
            #if 'upload'+str(i) in postdata or ('uploadfilename'+str(i) in postdata and 'uploadtext' + str(i) in postdata):
                #inputformat = None
                #if not 'uploadformat' + str(i) in postdata:
                    #raise BadRequest('No upload format specified') #TODO: message doesn't show to client
                #for f in settings.INPUTFORMATS:                
                    #if f.__class__.__name__ == postdata['uploadformat' + str(i)]:
                        #inputformat = f
            
                #if not inputformat:
                    #raise web.forbidden() 
            #else:
                #raise web.forbidden()

        #Project.create(project, user)


        #web.header('Content-Type', "text/xml; charset=UTF-8")
        #output = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        #output += "<clamupload uploads=\""+str(postdata['uploadcount'])+"\">\n"

        ##we may now assume all upload-data exists:
        #for i in range(1,int(postdata['uploadcount']) + 1):
            #if 'upload'+str(i) in postdata and (not 'uploadtext'+str(i) in postdata or not postdata['uploadtext' + str(i)]) and (not 'uploadurl'+str(i) in postdata or not postdata['uploadurl' + str(i)]):
                #output += "<upload seq=\""+str(i) +"\" filename=\""+postdata['upload' + str(i)].filename +"\">\n"

                #printdebug("Selecting client-side file " + postdata['upload' + str(i)].filename + " for upload")

                #filename = os.path.basename(postdata['upload' + str(i)].filename.lower())

                ##Is the upload an archive?
                #extension = filename.split(".")[-1]
                #if extension == "gz" or  extension == "bz2" or extension == "tar" or  extension == "zip":
                    #archive = True
                #else:                
                    ##upload not an archive:
                    #archive = False
                    #filename = inputformat.filename(filename) #set proper filename extension
                #realupload = True
                #wget = False
            #elif 'uploadtext'+str(i) in postdata and postdata['uploadtext' + str(i)]:
                #if 'uploadfilename'+str(i) in postdata and postdata['uploadfilename' + str(i)]:
                    #filename = postdata['uploadfilename' + str(i)]
                #else:
                    ##if no filename exists, make a random one
                    #filename =  "%032x" % random.getrandbits(128)
                #output += "<upload seq=\""+str(i) +"\" filename=\""+postdata['uploadfilename' + str(i)] +"\">\n"

                #archive = False
                #filename = inputformat.filename(postdata['uploadfilename' + str(i)]) #set proper filename extension
                #realupload = False
                #wget = False
            #elif 'uploadurl'+str(i) in postdata and postdata['uploadurl' + str(i)]:
                #if 'uploadfilename'+str(i) in postdata and postdata['uploadfilename' + str(i)]:
                    ##explicit filename passed
                    #filename = postdata['uploadfilename' + str(i)]
                #else:
                    ##get filename from URL:
                    #filename = os.path.basename(postdata['uploadurl' + str(i)])
                    #if not filename:
                        #filename =  "%032x" % random.getrandbits(128)  #make a random one

                #output += "<upload seq=\""+str(i) +"\" filename=\""+postdata['uploadurl' + str(i)] +"\">\n"

                #wget = True
                #realupload = False
                #filename = inputformat.filename(filename) #set proper filename extension


            #inputformat = None
            #for f in settings.INPUTFORMATS:                
                #if f.__class__.__name__ == postdata['uploadformat' + str(i)]:
                    #inputformat = f

            ##write trigger so the system knows uploads are in progress
            ##f = open(Project.path(project) + '.upload','w') 
            ##f.close()

            #printlog("Uploading '" + filename + "' (" + unicode(inputformat) + ", " + inputformat.encoding + ")")
            #printdebug("(start copy upload)" )
            ##upload file 
            ##if archive:
            #if inputformat.subdirectory:
                #if not os.path.isdir(inputformat.subdirectory ):
                    #os.mkdir(inputformat.subdirectory ) #TODO: make recursive and set mode
                #filename = inputformat.subdirectory  + "/" + filename
    
            #if wget:
                #try:
                    #req = urllib2.urlopen(postdata['uploadurl' + str(i)])
                #except:
                    #raise web.webapi.NotFound()
                #CHUNK = 16 * 1024
                #f = open(Project.path(project) + 'input/' + filename,'wb')
                #while True:
                    #chunk = req.read(CHUNK)
                    #if not chunk: break
                    #f.write(chunk)
            #elif realupload:
                #f = open(Project.path(project) + 'input/' + filename,'wb')
                #for line in postdata['upload' + str(i)].file:
                    #f.write(line) #encoding unaware, solves big-file upload problem?
            #else:
                #f = codecs.open(Project.path(project) + 'input/' + filename,'w', inputformat.encoding)
                #f.write(postdata['uploadtext' + str(i)])
            #f.close()
            #printdebug("(end copy upload)" )

            ##test uploaded files (this also takes care of extraction)
            #output += self.test(project, filename, inputformat)
            
            #output += "</upload>\n"

        #output += "</clamupload>"
         
        #return output #200



def usage():
        print >> sys.stderr, "Syntax: clamservice.py [options] clam.config.yoursystem"
        print >> sys.stderr, "Options:"
        print >> sys.stderr, "\t-d            - Enable debug mode"
        print >> sys.stderr, "\t-c            - Run in FastCGI mode"
        print >> sys.stderr, "\t-H [hostname] - Hostname"
        print >> sys.stderr, "\t-p [port]     - Port"
        print >> sys.stderr, "\t-h            - This help message"
        print >> sys.stderr, "\t-v            - Version information"
        print >> sys.stderr, "(Note: Do not invoke clamservice directly if you want to run in WSGI mode)"


def set_defaults(HOST = None, PORT = None):
    global LOG

    #Default settings
    settingkeys = dir(settings)

    if 'ROOT' in settingkeys and not settings.ROOT[-1] == "/":
        settings.ROOT += "/" #append slash

    if not 'USER' in settingkeys:
        settings.USER = None
    if not 'ADMINS' in settingkeys:
        settings.ADMINS = []
    if not 'PROJECTS_PUBLIC' in settingkeys:
        settings.PROJECTS_PUBLIC = True
    if not 'PROFILES' in settingkeys:
        settings.PROFILES = []
    if not 'PORT' in settingkeys and not PORT:
        settings.PORT = 80
    if not 'HOST' in settingkeys and not HOST:
        settings.HOST = os.uname()[1]
    if not 'URLPREFIX' in settingkeys:
        settings.URLPREFIX = ''    

    if 'LOG' in settingkeys: #set LOG
        LOG = open(settings.LOG,'a')

    for s in ['SYSTEM_ID','SYSTEM_DESCRIPTION','SYSTEM_NAME','ROOT','COMMAND','PROFILES']:    
        if not s in settingkeys:
            error("ERROR: Service configuration incomplete, missing setting: " + s)


def test_dirs():
    if not os.path.isdir(settings.ROOT):
        warning("Root directory does not exist yet, creating...")
        os.mkdir(settings.ROOT)
    if not os.path.isdir(settings.ROOT + 'corpora'):
        warning("Corpora directory does not exist yet, creating...")
        os.mkdir(settings.ROOT + 'corpora')
    if not os.path.isdir(settings.ROOT + 'projects'):
        warning("Projects directory does not exist yet, creating...")
        os.mkdir(settings.ROOT + 'projects')    
    elif not settings.PARAMETERS:
            warning("No parameters specified in settings module!")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        usage()
        sys.exit(1)

    settingsmodule = None
    fastcgi = False
    PORT = HOST = None


    try:
        opts, args = getopt.getopt(sys.argv[1:], "hdcH:p:v")
    except getopt.GetoptError, err:
        # print help information and exit:
        print str(err)
        usage()
        sys.exit(2)

    for o, a in opts:
        if o == '-d':
            DEBUG = True
            setdebug(True)
        elif o == '-c':
            fastcgi = True
        elif o == '-H':
            HOST = a
        elif o == '-p':
            PORT = int(a)
        elif o == '-h':
            usage()
            sys.exit(0)
        elif o == '-v':
            print "CLAM WebService version " + str(VERSION)
            sys.exit(0)
        else:
            usage()
            print "ERROR: Unknown option: ", o
            sys.exit(2)

    if (len(args) == 1):
        settingsmodule = args[0]
    elif (len(args) > 1):
        print >>sys.stderr, "ERROR: Too many arguments specified"
        usage()
        sys.exit(2)
    else:
        print >>sys.stderr, "ERROR: No settings module specified!"
        usage()
        sys.exit(2)



    


    import_string = "import " + settingsmodule + " as settings"
    exec import_string

    #Check version
    req = str(settings.REQUIRE_VERSION).split('.')
    ver = str(VERSION).split('.')

    uptodate = True
    for i in range(0,len(req)):
        if i < len(ver):
            if req[i] > ver[i]:
                uptodate = False
                break
            elif ver[i] > req[i]:
                break
    if not uptodate:
        error("Version mismatch: at least " + str(settings.REQUIRE_VERSION) + " is required")


    set_defaults(HOST,PORT)
    if HOST:
        settings.HOST = HOST
    test_dirs()

    #fake command line options for web.py
    sys.argv = [ sys.argv[0] ] 
    if PORT:
        sys.argv.append(str(PORT)) #port from command line
        settings.PORT = PORT                       
    elif 'PORT' in dir(settings):
        sys.argv.append(str(settings.PORT))

    # Create decorator
    #requirelogin = real_requirelogin #fool python :) 
    #if USERS:
    #    requirelogin = digestauth.auth(lambda x: USERS[x], realm=SYSTEM_ID)
    if settings.USERS:
        auth = clam.common.digestauth.auth(userdb_lookup, realm= settings.SYSTEM_ID)

    if not fastcgi:
        settings.URLPREFIX = '' #standalone server always runs at the root

    CLAMService('fastcgi' if fastcgi else '') #start

def run_wsgi(settingsmodule):
    global LOG
    """Run CLAM in WSGI mode"""
    #import_string = "import " + settingsmodule + " as settings"
    #exec import_string


    globals()['settings'] = settingsmodule

    set_defaults(None,None)
    if LOG == sys.stdout:
        #there is no stdout in WSGI mode, and the user didn't configure a log, discard output
        LOG = None
    test_dirs()

    if settings.USERS:
        auth = clam.common.digestauth.auth(userdb_lookup, realm= settings.SYSTEM_ID)

    service = CLAMService('wsgi')
    return service.application


