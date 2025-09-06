// Package cmd
package cmd

import (
	"github.com/sirupsen/logrus"
	"github.com/spf13/cobra"
)

var (
	dbURL       string
	csvFilepath string
	verbose     bool
	quiet       bool
	rootCmd     = &cobra.Command{
		Use:   "debate-parser",
		Short: "debate-parser is a parser for NSDA policy debate documents",
		PersistentPreRun: func(cmd *cobra.Command, args []string) {
			setupLogging()
		},
	}
)

func init() {
	// DB configuration
	rootCmd.PersistentFlags().StringVar(&dbURL, "db-url", "", "Postgres DB URL")

	// CSV configuration
	rootCmd.PersistentFlags().StringVar(&csvFilepath, "csv-path", "", "CSV file path")

	// Logging configuration
	rootCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "Enable verbose logging")
	rootCmd.PersistentFlags().BoolVarP(&quiet, "quiet", "q", false, "Suppress most output")

	rootCmd.MarkFlagsMutuallyExclusive("db-url", "csv-path")
	rootCmd.MarkFlagsOneRequired("db-url", "csv-path")
	rootCmd.MarkFlagsMutuallyExclusive("verbose", "quiet")
}

func setupLogging() {
	if quiet {
		logrus.SetLevel(logrus.WarnLevel)
	} else if verbose {
		logrus.SetLevel(logrus.DebugLevel)
		logrus.SetFormatter(&logrus.TextFormatter{
			FullTimestamp: true,
		})
	} else {
		logrus.SetLevel(logrus.InfoLevel)
		logrus.SetFormatter(&logrus.TextFormatter{
			DisableTimestamp: true,
		})
	}
}

func Execute() {
	if err := rootCmd.Execute(); err != nil {
		logrus.Fatal(err)
	}
}
