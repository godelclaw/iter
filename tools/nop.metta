(= (description) "Perform no action if task is complete and send was already used to report back results, do not re-send!")

(= (run) (progn (println! NOP) "SUCCESS"))
