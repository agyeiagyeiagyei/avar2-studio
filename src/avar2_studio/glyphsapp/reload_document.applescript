tell application "Glyphs"
    try
        set docPath to POSIX file "$abs_path" as alias
        set openDocs to documents whose path is (docPath as string)
        set docCount to count of openDocs

        if docCount > 0 then
            -- Step 1: Save any unsaved changes in all open windows
            -- Reference documents from the openDocs list
            repeat with aDoc in openDocs
                tell aDoc
                    if modified then
                        save
                    end if
                end tell
            end repeat

            -- Step 2: Close all windows of this document
            -- Close all documents from the openDocs list
            repeat with aDoc in openDocs
                tell aDoc
                    close saving no
                end tell
            end repeat

            -- Step 3: Wait for close to complete
            delay 0.5

            -- Step 4: Reopen the document
            open docPath
        end if
    end try
end tell
