!(import! &self "../lib_context.metta")
(= (description) "Unbounded output log: append all messages to a queryable file.")
(= (transform $messages $tools)
   (ctx:log-transform unbounded $messages $tools
     (system:getenv-or-default "ITER_LOG_PATH" "unbounded_log.txt")
     (system:getenv-or-default "ITER_LOG_STATE" ".log_state")))
