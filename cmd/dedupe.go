package cmd

import (
	"debate-parser/internal"

	"github.com/sirupsen/logrus"
	"github.com/spf13/cobra"
)

var dedupeCmd = &cobra.Command{
	Use:   "dedupe",
	Short: "dedupe a storage of debate cards",
	Args:  cobra.ExactArgs(0),
	RunE: func(cmd *cobra.Command, args []string) error {
		var storage internal.CardStorage
		if csvFilepath != "" {
			logrus.WithField("path", csvFilepath).Info("Using CSV storage")
			storage = internal.NewCSVStorage(csvFilepath)
		} else if dbURL != "" {
			logrus.WithField("url", dbURL).Info("Using PostgreSQL storage")
			s, err := internal.NewPostgresStorage(dbURL)
			storage = s
			if err != nil {
				logrus.WithError(err).Error("Failed to connect to database")
				return err
			}
		}
		defer storage.Close()

		logrus.Info("Starting duplicate removal process")
		err := storage.PruneDupes()
		if err != nil {
			logrus.WithError(err).Error("Failed to remove duplicates")
			return err
		}

		logrus.Info("Deduplication completed successfully")
		return nil
	},
}

func init() {
	rootCmd.AddCommand(dedupeCmd)
}
