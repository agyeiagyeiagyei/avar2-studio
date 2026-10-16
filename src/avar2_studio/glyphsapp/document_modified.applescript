tell application "Glyphs"
    try
        set docPath to POSIX file "$abs_path" as alias
        set openDocs to documents whose path is (docPath as string)
        set docCount to count of openDocs

        if docCount > 0 then
            repeat with aDoc in openDocs
                tell aDoc
                    if modified then
                        return true
                    end if
                end tell
            end repeat
        end if
        return false
    on error
        return false
    end try
end tell
