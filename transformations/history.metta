!(import! &self "../lib_context.metta")
(= (description) "Stores episodes with persistent window deduplication; log and state writes are not atomic.")
(= (transform $messages $tools)
   (ctx:history-transform $messages $tools
     (system:getenv-or-default "ITER_HISTORY_PATH" "history.metta")
     (system:getenv-or-default "ITER_HISTORY_STATE" ".history_state")
     (system:format-time "%Y-%m-%d %H:%M:%S" (system:wall-time) local)))
