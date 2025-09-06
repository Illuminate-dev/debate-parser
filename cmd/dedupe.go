package cmd

import (
	"debate-parser/internal"

	"github.com/spf13/cobra"
)

var dedupeCmd = &cobra.Command{
	Use:   "dedupe",
	Short: "dedupe a storage of debate cards",
	Args:  cobra.ExactArgs(0),
	RunE: func(cmd *cobra.Command, args []string) error {
		var storage internal.CardStorage
		if csvFilepath != "" {
			storage = internal.NewCSVStorage(csvFilepath)
		} else if dbURL != "" {
			s, err := internal.NewPostgresStorage(dbURL)
			storage = s
			if err != nil {
				return err
			}
		}
		defer storage.Close()

		return storage.PruneDupes()
	},
}

func init() {
	rootCmd.AddCommand(dedupeCmd)
}
