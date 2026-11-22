;; Optional Iter dynamic tool: update the terminal's pinned side panel.
;;
;; Copy this file to tools/ui.metta to enable it. It writes the same atomic
;; .iter-ui-pin.json state as upstream log.py / tools/ui.py.
(= (description) "Set or clear the persistent right-side terminal panel (not a chat send). Content supports ANSI SGR colors: JSON \\u001b[31m red, \\u001b[92m green, \\u001b[38;5;208m orange, \\u001b[0m reset; also 24-bit RGB. Empty content clears the panel.")
(= (parameters) (title content))

(= (run $title $content)
   (if (not (== $content ""))
       (case (iter-log:pin $content $title)
         ((true "Terminal pinned panel updated.")
          ($err $err)))
       (case (iter-log:clear-pin)
         ((true "Terminal pinned panel cleared.")
          ($err $err)))))
