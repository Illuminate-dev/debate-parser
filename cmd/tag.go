package cmd

import (
	"fmt"
	"path/filepath"
	"strings"

	"debate-parser/internal"

	"github.com/sirupsen/logrus"
	"github.com/spf13/cobra"
)

var tagCmd = &cobra.Command{
	Use:   "tag <docx file> <tag1,tag2,tag3...>",
	Short: "tag cards from a document with specified tags",
	Args:  cobra.ExactArgs(2),
	RunE: func(cmd *cobra.Command, args []string) error {
		inputFile := args[0]
		tagsStr := args[1]
		
		// Parse tags from comma-separated string (CLI input uses commas)
		tags := strings.Split(tagsStr, ",")
		for i, tag := range tags {
			tags[i] = strings.TrimSpace(tag)
		}
		
		logrus.WithFields(logrus.Fields{
			"file": inputFile,
			"tags": tags,
			"ext":  filepath.Ext(inputFile),
		}).Info("Starting tag operation")

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

		tagger := internal.NewTagger(storage)

		switch filepath.Ext(inputFile) {
		case ".docx":
			logrus.Info("Tagging cards from single DOCX file")
			err := tagger.TagFromDocx(inputFile, tags)
			if err != nil {
				logrus.WithError(err).Error("Failed to tag cards from DOCX file")
				return err
			}
		case ".zip":
			logrus.Info("Tagging cards from ZIP archive containing DOCX files")
			err := tagger.TagFromZip(inputFile, tags)
			if err != nil {
				logrus.WithError(err).Error("Failed to tag cards from ZIP file")
				return err
			}
		default:
			logrus.WithField("ext", filepath.Ext(inputFile)).Error("Unsupported file type")
			return fmt.Errorf("unsupported file type: %s", filepath.Ext(inputFile))
		}

		logrus.Info("Tag operation completed successfully")
		return nil
	},
}

func init() {
	rootCmd.AddCommand(tagCmd)
}