!(import! &self "../lib_context.metta")
(= (description) "Maintains transcript of textual communication messages only.")
(= (transform $messages $tools)
   (ctx:log-transform transcript $messages $tools
     (system:getenv-or-default "ITER_TRANSCRIPT_PATH" "transcript.txt")
     (system:getenv-or-default "ITER_TRANSCRIPT_STATE" ".transcript_state")))
