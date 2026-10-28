!(import! &self "../lib_formalizations.metta")
(= (description) "Updates an existing space text file from Formalization lines; extracted text is not evaluated or proof-checked.")
(= (transform $messages $tools)
   (forms:transform $messages $tools
     (system:getenv-or-default "ITER_SPACE_PATH" "transformations/.runtime/space.metta")))
