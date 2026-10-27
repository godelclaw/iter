;; Iter's dynamic worker in MeTTa, replacing utils.py --invoke: one CeTTa
;; process per call. ITER_WORKER_REQUEST names a JSON file holding the module,
;; the function, its args and kwargs, and the file for the result. A fault or a
;; hang stays in this process, which the loop times out and kills.
;;   tools:           (description) (parameters) (run $parameter ...)
;;   channels:        (receive) and optionally (send $content)
;;   transformations: (description) (transform $messages $tools)
;; (parameters) lists run's parameter names in order; it may be left out when
;; run takes none. Modules see utils.metta's helpers.
!(import! &self utils)

(= (worker:failure $text) (worker:failed $text))

;; A call of an undefined function answers itself; one with too few
;; arguments answers a partial application.
(= (worker:apply $name $args)
   (let* (($call (cons-atom $name $args))
          ($value (eval $call)))
     (case $value
       (((partial $name $given) (worker:failure (atomics_to_string ("TypeError: (" $name ") takes more arguments than " (size-atom $args)))))
        ($other (if (== $other $call)
                    (worker:failure (atomics_to_string ("AttributeError: the module defines no " $name " of " (size-atom $args) " arguments")))
                    $other))))))

(= (worker:parameters)
   (let* (($call (cons-atom parameters ()))
          ($declared (eval $call)))
     (if (== $declared $call)
         ()
         (map-atom $declared $name (if (is-string $name) (string-to-atom $name) $name)))))

;; run(**kwargs) of Python: the values in the order of (parameters); a
;; missing or an unexpected name is an error, as it was a TypeError.
(= (worker:run $kwargs)
   (let* (($names (worker:parameters))
          ($keys (json-keys $kwargs))
          ($missing (filter-atom $names $name (not (is-member $name $keys))))
          ($unexpected (filter-atom $keys $key (not (is-member $key $names)))))
     (if (not (== $missing ()))
         (worker:failure (atomics_to_string ("TypeError: run() missing required arguments: " (join-text ", " $missing))))
         (if (not (== $unexpected ()))
             (worker:failure (atomics_to_string ("TypeError: run() got unexpected keyword arguments: " (join-text ", " $unexpected))))
             (worker:apply run (map-atom $names $name (json-get $kwargs $name null)))))))

;; str(result) of Python for every function but transform; () stands for None,
;; and an (Error $what $why) answer fails the call as an exception did.
(= (worker:message $value) (if (is-string $value) $value (repr $value)))
(= (worker:text $value)
   (case $value
     (((worker:failed $text) $value)
      ((Error $what $why) (worker:failure (atomics_to_string ((worker:message $what) ": " (worker:message $why)))))
      (() null)
      ($other (worker:message $other)))))

(= (worker:call $function $args $kwargs)
   (case $function
     (("__tool_metadata__"
       (let* (($description (worker:apply description ()))
              ($names (worker:parameters)))
         (case $description
           (((worker:failed $text) $description)
            ($text (json-object ((json-pair description (worker:text $text))
                                 (json-pair parameters (map-atom $names $name (text_to_string $name))))))))))
      ("__description__" (worker:text (worker:apply description ())))
      ("run" (worker:text (worker:run $kwargs)))
      ("transform" (let $result (worker:apply transform $args)
                        (case $result
                          (((Error $what $why) (worker:text $result))
                           ($other $other)))))
      ($other (worker:text (worker:apply (string-to-atom $function) $args))))))

;; import! answers nothing for a missing or malformed module.
(= (worker:load $module)
   (if (file-exists $module)
       (case (collapse (import! &self $module))
         (((true) loaded)
          ($none (worker:failure (atomics_to_string ("ImportError: " $module " could not be loaded: check its parentheses, quotes and directives"))))))
       (worker:failure (atomics_to_string ("FileNotFoundError: " $module)))))

(= (worker:outcome $outcome)
   (case $outcome
     ((((worker:failed $text)) (json-object ((json-pair ok false) (json-pair error $text))))
      (((Error $type $context)) (json-object ((json-pair ok false) (json-pair error (repr (Error $type $context))))))
      (((Error $error)) (json-object ((json-pair ok false) (json-pair error (repr $error)))))
      (($value) (json-object ((json-pair ok true) (json-pair result $value))))
      ($none (json-object ((json-pair ok false) (json-pair error "the call produced no result")))))))

(= (worker:serve $request)
   (let* (($module (progn (translatePredicate (absolute_file_name (json-get $request module "") $path)) (text_to_string $path)))
          ($function (json-get $request function ""))
          ($args (json-get $request args ()))
          ($kwargs (json-get $request kwargs (json-object ()))))
     (case (worker:load $module)
       ((loaded (worker:call $function $args $kwargs))
        ($failure $failure)))))

!(let* (($request (json-parse (read-text (getenv-default "ITER_WORKER_REQUEST" ""))))
        ($outcome (collapse (catch (once (worker:serve $request))))))
   (write-text (json-get $request result "") (json-text (worker:outcome $outcome))))
