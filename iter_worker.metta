;; Iter's dynamic worker in MeTTa, replacing utils.py --invoke: one CeTTa
;; process per call. Its program argument names a JSON request file holding
;; the module, the function, its args and kwargs, and the file for the result.
;; A fault or a hang stays in this process, which the loop times out and kills.
;;   tools:           (description) (parameters) (run $parameter ...)
;;   channels:        (receive) and optionally (send $content)
;;   transformations: (description) (transform $messages $tools)
;; (parameters) lists run's parameter names in order; it may be left out when
;; run takes none. Modules see utils.metta's helpers and the libraries it imports.
!(import! &self utils)

;; A call of an undefined function answers itself; one with too few
;; arguments answers a partial application.
(= (worker:apply $name $args)
   (let* (($call (cons-atom $name $args))
          ($value (eval $call)))
     (case $value
       (((partial $function $given) (worker:failed (str-cat ("TypeError: (" $name ") takes more arguments than " (size-atom $args)))))
        ($other (if (== $other $call)
                    (worker:failed (str-cat ("AttributeError: the module defines no " $name " of " (size-atom $args) " arguments")))
                    $other))))))

;; the names of run's parameters, as strings
(= (worker:parameters)
   (let* (($call (cons-atom parameters ()))
          ($declared (eval $call)))
     (if (== $declared $call) () (map-atom $declared $name (text $name)))))

;; run(**kwargs) of Python: the values in the order of (parameters); a
;; missing or an unexpected name is an error, as it was a TypeError.
(= (worker:run $kwargs)
   (let* (($names (worker:parameters))
          ($keys (object-keys $kwargs))
          ($missing (filter-atom $names $name (not (is-member $name $keys))))
          ($unexpected (filter-atom $keys $key (not (is-member $key $names)))))
     (if (not (== $missing ()))
         (worker:failed (str-cat ("TypeError: run() missing required arguments: " (str:join ", " $missing))))
         (if (not (== $unexpected ()))
             (worker:failed (str-cat ("TypeError: run() got unexpected keyword arguments: " (str:join ", " $unexpected))))
             (worker:apply run (map-atom $names $name (json:get $kwargs $name)))))))

;; str(result) of Python for every function but transform; () stands for None,
;; and an (Error $what $why) answer fails the call as an exception did.
(= (worker:text $value)
   (case $value
     (((worker:failed $why) $value)
      ((Error $what $why) (worker:failed (error_text $value)))
      (() JsonNull)
      ($other (text $other)))))

;; transform answers ($messages $tools), returned as the JSON array of the two
(= (worker:transformed $value)
   (case $value
     (((worker:failed $why) $value)
      ((Error $what $why) (worker:failed (error_text $value)))
      ($other (if (== (get-metatype $other) Expression) (JsonArray $other) $other)))))

(= (worker:call $function $args $kwargs)
   (case $function
     (("__tool_metadata__"
       (case (worker:text (worker:apply description ()))
         (((worker:failed $why) (worker:failed $why))
          ($description (JsonObject ((JsonPair "description" $description)
                                     (JsonPair "parameters" (JsonArray (worker:parameters)))))))))
      ("__description__" (worker:text (worker:apply description ())))
      ("run" (worker:text (worker:run $kwargs)))
      ("transform" (worker:transformed (worker:apply transform $args)))
      ($other (worker:text (worker:apply (str:string->symbol $function) $args))))))

;; import! answers nothing for a missing or malformed module.
(= (worker:load $module)
   (if (fs:is-file $module)
       (let $path (fs:canonical-path $module)
            (case (collapse (import! &self $path))
              (((true) loaded)
               ($none (worker:failed (str-cat ("ImportError: " $module " could not be loaded: check its parentheses, quotes and directives")))))))
       (worker:failed (str-cat ("FileNotFoundError: " $module)))))

(= (worker:serve $request)
   (let* (($module (json:get $request "module"))
          ($function (json:get $request "function"))
          ($args (json:array-items (json:get $request "args")))
          ($kwargs (json:get $request "kwargs")))
     (case (worker:load $module)
       ((loaded (worker:call $function $args $kwargs))
        ($failure $failure)))))

(= (worker:outcome $outcome)
   (case $outcome
     ((((worker:failed $why)) (JsonObject ((JsonPair "ok" false) (JsonPair "error" $why))))
      (($value) (JsonObject ((JsonPair "ok" true) (JsonPair "result" $value))))
      ($none (JsonObject ((JsonPair "ok" false) (JsonPair "error" "the call produced no result")))))))

;; json.dumps of the outcome; a value JSON cannot hold fails the call
(= (worker:json $output)
   (let $json (json:stringify $output)
        (if (is-string $json)
            $json
            (json:stringify (JsonObject ((JsonPair "ok" false)
                                         (JsonPair "error" (str-cat ("Result serialization failed: " (index-atom $json (- (size-atom $json) 1)))))))))))

;; A Python module runs in this process's Python, which writes the result.
(= (worker:python $request_file)
   (let $helper (import! &self iter_python.py)
        (py-call (iter_python.invoke $request_file))))

!(let* (($request_file (car-atom (system:args)))
        ($request (json:parse (fs:read-text $request_file))))
   (if (str:ends-with (json:get $request "module") ".py")
       (worker:python $request_file)
       (let $outcome (collapse (once (worker:serve $request)))
            (fs:write (json:get $request "result") (worker:json (worker:outcome $outcome))))))
