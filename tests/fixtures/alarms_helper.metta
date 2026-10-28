;; Calls the alarm helper with an explicit directory, time and timestamp, and
;; answers the transformed messages and tools as JSON text. A failure answers
;; its (Error ...), which the worker reports as a failed call. The worker
;; protocol calls any function by name. Paths are relative to the Iter
;; workspace, where modules run.
!(import! &self "transformations/alarms.metta")
(= (helper $messages $tools $directory $now $stamp)
   (case (alarms:transform-at $messages $tools $directory $now $stamp)
     ((($new $kept) (json:stringify (JsonArray ($new $kept))))
      ($failure $failure))))
