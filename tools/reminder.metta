;; Same local calendar and file protocol as the reference reminder tool.
!(import! &self "../lib_calendar.metta")
(= (description) "Create an alarm: when is a local YYYY-MM-DD HH:MM:SS date; channel defaults to terminal and message to empty text.")
(= (parameters) (when channel message))
(= (defaults) (JsonObject ((JsonPair "channel" "terminal") (JsonPair "message" ""))))
(= (reminder:directory) (system:getenv-or-default "ITER_ALARMS_DIR" "memory/alarms"))
(= (reminder:absolute-directory $directory)
   (if (str:starts-with $directory "/") $directory
       (str:join "/" ((system:cwd) $directory))))
(= (run $when $channel $message)
   (let $directory (reminder:directory)
     (case (fs:make-directories $directory 511)
       ((()
         (case (calendar:timestamp $when)
           (((Error $what $why) (Error "reminder" (error_text (Error $what $why))))
            ($timestamp (reminder:write $directory $timestamp $when $channel $message)))))
        ($failed (Error "reminder" (str-cat ("cannot create alarm directory: " $failed))))))))

(= (reminder:write $directory $timestamp $when $channel $message)
   (let $path (str:join "/" ((reminder:absolute-directory $directory) (str:number->string $timestamp)))
     (case (fs:write $path (str:join "\n" ($channel $message)))
       ((() (str-cat ("SUCCESS, RETURN: Reminder set for " $when " (timestamp " $timestamp ") -> " $path
                      "\n  Channel: " $channel "\n  Message: " $message)))
        ($failed (Error "reminder" (str-cat ("cannot write alarm: " $failed))))))))
