"use strict";

var Module = {
    'locateFile': function (name) {
        return ThingView.modulePath + name;
    },
    onRuntimeInitialized : function () {
        ThingView.loaded = true;
        if (!(ThingView.initCB == undefined)) {
            ThingView._completeInit();
            ThingView._setResourcePath(ThingView.resourcePath);
            ThingView.LoadPreferences(function(jsonObj, defaultPrefs) {
                if (jsonObj !== undefined) {
                    ThingView.StorePreferences(jsonObj, defaultPrefs);
                    _addPreferenceEvents();
                }
                if (ThingView.initCB) {
                    ThingView.initCB();
                }
            });
        }
    }
};

function FailedLoad()
{
    window.alert("In FailedLoad");
}
var ThingView = (function () {
    var id = 0;
    var thingView;
    var isUpdated = false;
    var _currentSession = null;
    var _viewable = null;
    var _nextCanvasId = 0;
    var resourcePath = null;
    var loadedPreferences = {};
    var defaultPreferences = {};
    var s_fileversion = "0.32.3.0";
    var s_productversion = "0.32.3-L5FDTR";
    var s_productname = "ThingView 0.32";
    var doCapture = false;
    var captureWrapper;
    var requestID = null;
    var iOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
    var edge = /Edge\/\d+/.test(navigator.userAgent);
    var thingView2dScript = "";

    // Preference names
    var s_pref_nav_navmode = "Nav.NavMode";
    var s_pref_gen_filecache = "Gen.FileCache";
    var s_pref_gen_filecachesize = "Gen.FileCacheSize";

    var returnObj = {
        init: function (path, initCB) {
            ThingView.resourcePath = path;
            ThingView.initCB = initCB;
            if (ThingView.loaded) {
                ThingView._completeInit();
                ThingView.LoadPreferences(function(jsonObj, defaultPrefs) {
                    if (jsonObj !== undefined) {
                        ThingView.StorePreferences(jsonObj, defaultPrefs);
                        _addPreferenceEvents();
                    }
                    if (ThingView.initCB) {
                        ThingView.initCB();
                    }
                });
            }
            else {
                var head = document.getElementsByTagName('head').item(0);
                ThingView.id = document.createElement("SCRIPT");
                var loaderLib;
                if ( (typeof(WebAssembly) == "undefined") || (iOS == true) || (edge == true))
                    loaderLib = "libthingview.js";
                else
                {
                    loaderLib = "libthingview_wasm.js";
                    ThingView.id.onerror = this.failedWasmLoad;
                }

                if (path) {
                    var idx = path.lastIndexOf('/');
                    if ((idx == -1) ||  (idx < path.length-1))
                        path += "/";
                    loaderLib = path + loaderLib;
                    ThingView.modulePath = path;
                }
                ThingView.id.src = loaderLib;
                head.appendChild(ThingView.id);
            }
        },
        failedWasmLoad: function() {
            console.log("Failed loading wasm so try asmjs");
            var head = document.getElementsByTagName('head').item(0);

            var id = document.createElement("SCRIPT");
            id.src =ThingView.modulePath + "libthingview.js";
            head.appendChild(id);

        },
        GetVersion: function() {
            return s_version;
        },

        GetDateCode: function() {
                return thingView.GetDateCode();
        },
        GetFileVersion: function() {
            return s_fileversion;
        },
        _completeInit: function () {
            thingView = Module.ThingView.GetThingView();
            if (requestID == null)
                requestID = requestAnimationFrame(_DoRender);
        },
        _setResourcePath: function(path) {
            thingView.SetResourcePath(path);
        },
        SetInitFlags: function (flags) {
            thingView.SetInitFlags(flags);
        },
        SetSystemPreferencesFromJson: function (prefstr) {
            thingView.SetSystemPreferencesFromJson(prefstr);
        },
        LoadImage: function (imagename) {
            thingView.LoadImage(imagename);
        },
        CreateSession: function(parentCanvasId) {
            var session = _createSession(parentCanvasId);
            if (ThingView.loadedPreferences) {
                if (Object.keys(ThingView.loadedPreferences).length > 0) {
                    _applyPreferences(session, ThingView.loadedPreferences);
                }
            }
            return session;
        },
        SetHighMemoryUsageValue: function (megaBytes) {
            thingView.SetHighMemoryUsageValue(megaBytes);
        },
        ClearCanvas: function (){
            _ClearCanvas();
        },
        EnableSession: function(session) {
            _enableSession(session);
        },
        DeleteSession: function(session) {
            _deleteSession(session);
        },
        Hide3DCanvas: function(session) {
            if (session) {
                _hide3DCanvas(session);  
            } else {
                _hide3DCanvas(_currentSession);
            }
        },
        Show3DCanvas: function(session) {
            if (session) {
                _show3DCanvas(session);  
            } else {
                _show3DCanvas(_currentSession);
            }
        },
        
        OpenPreferencesDialog: function() {
            window.open(ThingView.modulePath + "preferences.html", "ThingView Preferences", "width=500, height=250, status=no, toolbar=no, menubar=no, location=no");
        },
        StorePreferences: function(jsonObj, defaultPrefs) {
            try {
                if (!(jsonObj == undefined)) {
                    ThingView.loadedPreferences = jsonObj;
                }
                if (!(defaultPrefs == undefined)) {
                    ThingView.defaultPreferences = defaultPrefs;
                }
            } catch (e) {
                console.log("StorePreferences, exception: " + e);
            }
        },
        LoadPreferences: function(callbackFunc) {
            _loadPreferences(function(jsonObj, defaultPrefs) {
                callbackFunc(jsonObj, defaultPrefs);
            });
        },
        ApplyPreferences: function(jsonObj) {
            _applyPreferences(_currentSession, jsonObj);
        },
        SavePreferences: function(jsonObj) {
        },
        GetLoadedPreferences: function() {
            return _getLoadedPreferences();
        },
        CaptureCanvas: function(captureFunc) {
            doCapture = true;
            captureWrapper = captureFunc;
        },
        GetNextCanvasID: function() {
            var returnID = _nextCanvasId;
            _nextCanvasId++;
            return returnID;
        },
        LoadDocument: function(viewable, parentCanvasId, model, callback) {
            if (thingView2dScript == "") {
                thingView2dScript = document.createElement("SCRIPT");
                thingView2dScript.src = ThingView.resourcePath ? ThingView.resourcePath + "thingview2d.js" : "thingview2d.js";
                thingView2dScript.onload = function(){
                    ThingView.LoadDocument(viewable, parentCanvasId, model, callback);
                }
                document.getElementsByTagName('head').item(0).appendChild(thingView2dScript);
            }
        },
        IsPDFSession: function() {
            return false;
        },
        IsSVGSession: function() {
            return false;
        }
    };
    return returnObj;// End of public functions

    function _DoRender(timeStamp) {
        var doRender = true;
        try
        {
            if((doCapture === true) && (captureWrapper !== undefined) && (captureWrapper instanceof Function)) {
                doCapture = false;
                captureWrapper(function() {
                    thingView.DoRender(timeStamp);
                });
            } else {
                thingView.DoRender(timeStamp);
            }
        } catch (err)
        {
            console.log("Javascript caught exception "+ err);
            doRender = false;
        }
        if (doRender)
            requestID = requestAnimationFrame(_DoRender);
    }

    function _createSession(parentCanvasId)
    {
        var sessionCanvas = document.createElement("canvas");
        var parent = document.getElementById(parentCanvasId);
        sessionCanvas.id = parentCanvasId + "_CreoViewCanvas" + _nextCanvasId;
        _nextCanvasId++;
        var posStyle = "position: relative; width: 100%; height: 100%;";
        var selStyle = "-moz-user-select: none; -webkit-user-select: none; -ms-user-select: none; user-select: none;";
        sessionCanvas.setAttribute('style', posStyle + selStyle);

        var width = parent.clientWidth;
        var height = parent.clientHeight;

        sessionCanvas.width = width;
        sessionCanvas.height = height;
        parent.insertBefore(sessionCanvas, parent.childNodes[0]);

        sessionCanvas.oncontextmenu = function (e) {
            e.preventDefault();
            return false;
        };
        _currentSession = thingView.CreateSession(sessionCanvas.id);
        return _currentSession;
    }
    
    function _ClearCanvas() {
        if (_IsPDFSession()) {
            var session_html = Module.castToSession_html(_currentSession);
            var canvasId = session_html.GetCanvasName();
            var canvas = document.getElementById(canvasId);
            var context = canvas.getContext('2d');
            if (context) {
                context.clearRect(0, 0, canvas.width, canvas.height);
            }
        }
    }

    function _enableSession(session)
    {
        if (_currentSession != null)
        {
            _currentSession.Disable();
        }
        session.Enable();
        _currentSession = session;
    }

    function _deleteSession(session) {
        if (_currentSession == session) {
            _currentSession = null;
        }
        var session_html = Module.castToSession_html(session);
        var canvasId = session_html.GetCanvasName();
        var canvas = document.getElementById(canvasId);
        session.delete();
        session_html.delete();
        if (canvas != null && canvas.parentElement != null)
            canvas.parentElement.removeChild(canvas);

    }
    
    function _loadPreferences(callback) {
        callback();
    }

    function _applyPreferences(session, jsonObj) {
        try {
        if (session == null)
            return;
        if (jsonObj !== undefined) {
            for (key in jsonObj) {
                if (ThingView.loadedPreferences === undefined)
                    ThingView.loadedPreferences = {};
                ThingView.loadedPreferences[key] = jsonObj[key];

                var fileCacheEnabled = false;
                var fileCacheSize = 0;

                if (key == s_pref_nav_navmode) {
                    if (jsonObj[key] == "CREO_VIEW") {
                        session.SetUpDirection("Y");
                        session.SetNavigationMode(Module.NavMode.CREO_VIEW);
                        if (!session.IsOrthographic())
                            session.SetOrthographicProjection(1.0);
                    }
                    else if (jsonObj[key] == "CREO") {
                        session.SetUpDirection("Y");
                        session.SetNavigationMode(Module.NavMode.CREO);
                        if (!session.IsOrthographic())
                            session.SetOrthographicProjection(1.0);
                    }
                    else if (jsonObj[key] == "CATIA") {
                        session.SetUpDirection("Y");
                        session.SetNavigationMode(Module.NavMode.CATIA);
                        if (!session.IsOrthographic())
                            session.SetOrthographicProjection(1.0);
                    }
                    else if (jsonObj[key] == "EXPLORE") {
                        session.SetUpDirection("Z");
                        session.SetNavigationMode(Module.NavMode.EXPLORE);
                        if (!session.IsPerspective())
                            session.SetPerspectiveProjection(60.0);
                    }
                    else if (jsonObj[key] == "MOCKUP")
                        session.SetNavigationMode(Module.NavMode.MOCKUP);
                    else if (jsonObj[key] == "VUFORIA")
                        session.SetNavigationMode(Module.NavMode.VUFORIA);
                    else if (jsonObj[key] == "VUFORIA_NOPICK")
                        session.SetNavigationMode(Module.NavMode.VUFORIA_NOPICK);
                } else if (key == s_pref_gen_filecache) {
                    if (jsonObj[key] === true)
                        fileCacheEnabled = true;
                } else if (key == s_pref_gen_filecachesize) {
                    fileCacheSize = jsonObj[key];
                }
                //if (fileCacheEnabled)
                  //  session.EnableFileCache(fileCacheSize);
            }
        }
        } catch (e) {
            console.log(e);
        }
    }
    
    function _hide3DCanvas(session){
        var session_html = Module.castToSession_html(session);
        var canvasId = session_html.GetCanvasName();
        var canvas = document.getElementById(canvasId);
        canvas.setAttribute('style', "width: 0%; height: 0%");
    }
    
    function _show3DCanvas(session){
        var session_html = Module.castToSession_html(session);
        var canvasId = session_html.GetCanvasName();
        var canvas = document.getElementById(canvasId);
        canvas.setAttribute('style',"width: 100%; height: 100%");
        canvas.parentNode.style.overflow = "";
    }

    function _getLoadedPreferences() {
        if (ThingView.loadedPreferences) {
            if (Object.keys(ThingView.loadedPreferences).length > 0) {
                return ThingView.loadedPreferences;
            }
        }
        return {};
    }

})();

function _addPreferenceEvents() {
    var re = new RegExp("version\\/(\\d+).+?safari", "i");
    var safari = navigator.userAgent.match(re);
    document.addEventListener("keydown", function(event) {
        if (safari) {
            if (event.ctrlKey && event.code == 'KeyP') {
                ThingView.OpenPreferencesDialog();
            }
        } else {
            if (event.shiftKey && (event.code == 'KeyP' || event.keyCode == 80 /*P*/)) {
                ThingView.OpenPreferencesDialog();
            }
        }
    }, false);

    window.addEventListener("storage", function(event) {
        if (event.key == 'msgPref') {
            if (event.newValue) {
                var message = JSON.parse(event.newValue);
                ThingView.ApplyPreferences(message);
                ThingView.SavePreferences(ThingView.GetLoadedPreferences());
            }
        } else if (event.key == 'resetPref') {
            if (event.newValue && event.newValue == 'true') {
                ThingView.loadedPreferences = {};
                ThingView.ApplyPreferences(ThingView.defaultPreferences);
                ThingView.SavePreferences(ThingView.GetLoadedPreferences());
            }
        } else if (event.key == 'msgReady') {
            if (event.newValue && event.newValue == 'true') {
                localStorage.setItem('msgCurPref', JSON.stringify(ThingView.loadedPreferences));
                localStorage.removeItem('msgCurPref');
                localStorage.setItem('msgDefPref', JSON.stringify(ThingView.defaultPreferences));
                localStorage.removeItem('msgDefPref');
            }
        }
    }, false);
}
